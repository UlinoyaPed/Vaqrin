#define _GNU_SOURCE
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sched.h>
#include <sys/mman.h>
#include <time.h>
#include <ucontext.h>
#include <unistd.h>

enum task_status { TASK_OK, TASK_RUNNING, TASK_JOINED, TASK_CLOSED, TASK_INTERRUPTED, TASK_SELF };
enum wait_kind { WAIT_NONE, WAIT_TASK, WAIT_GROUP, WAIT_TIME };

struct task;
struct worker;

struct queue {
    struct task *first;
    struct task *last;
};

struct group {
    struct worker *workers;
    size_t worker_count;
    struct task *tasks;
    struct queue incoming;
    size_t pending;
    size_t references;
    bool cancelled;
    bool stopping;
    bool closed;
    void (*report)(uint32_t);
};

struct worker {
    struct group *group;
    pthread_t thread;
    ucontext_t scheduler;
    struct queue ready;
};

struct task {
    struct group *group;
    struct worker *worker;
    struct task *next_group;
    struct task *next_ready;
    struct task *next_waiting;
    void (*run)(void *);
    void (*drop_result)(void *);
    void *work;
    void *result;
    void *mapping;
    size_t mapping_size;
    size_t guard_size;
    size_t references;
    ucontext_t context;
    enum wait_kind wait_kind;
    const void *awaited;
    struct timespec deadline;
    uint32_t error;
    bool interruptible;
    bool finished;
    bool returned;
    bool claimed;
    bool cancelled;
};

static pthread_mutex_t mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t changed = PTHREAD_COND_INITIALIZER;
static struct task *waiting;
static _Thread_local struct task *current;

static void enqueue(struct queue *queue, struct task *task) {
    task->next_ready = NULL;
    if (queue->last) queue->last->next_ready = task;
    else queue->first = task;
    queue->last = task;
}

static struct task *dequeue(struct queue *queue) {
    struct task *task = queue->first;
    if (!task) return NULL;
    queue->first = task->next_ready;
    if (!queue->first) queue->last = NULL;
    task->next_ready = NULL;
    return task;
}

static bool earlier(struct timespec a, struct timespec b) {
    return a.tv_sec < b.tv_sec || (a.tv_sec == b.tv_sec && a.tv_nsec < b.tv_nsec);
}

static void awaken(struct task **entry) {
    struct task *task = *entry;
    *entry = task->next_waiting;
    task->next_waiting = NULL;
    task->wait_kind = WAIT_NONE;
    enqueue(&task->worker->ready, task);
    pthread_cond_broadcast(&changed);
}

static void awaken_waiters(enum wait_kind kind, const void *awaited) {
    for (struct task **entry = &waiting; *entry;) {
        struct task *task = *entry;
        if (task->wait_kind == kind && task->awaited == awaited) awaken(entry);
        else entry = &task->next_waiting;
    }
}

static void awaken_cancelled(void) {
    for (struct task **entry = &waiting; *entry;) {
        struct task *task = *entry;
        if (task->interruptible && task->cancelled) awaken(entry);
        else entry = &task->next_waiting;
    }
}

static bool expire_timers(struct timespec *next) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now)) abort();
    bool has_next = false;
    for (struct task **entry = &waiting; *entry;) {
        struct task *task = *entry;
        if (task->wait_kind == WAIT_TIME) {
            if (!earlier(now, task->deadline)) {
                awaken(entry);
                continue;
            }
            if (!has_next || earlier(task->deadline, *next)) {
                *next = task->deadline;
                has_next = true;
            }
        }
        entry = &task->next_waiting;
    }
    return has_next;
}

static void suspend(void) {
    if (swapcontext(&current->context, &current->worker->scheduler)) abort();
}

// Called with the scheduler mutex held, and returns with it held.
static void wait_for(enum wait_kind kind, const void *target, bool interruptible) {
    if (!current) {
        pthread_cond_wait(&changed, &mutex);
        return;
    }
    current->wait_kind = kind;
    current->awaited = target;
    current->interruptible = interruptible;
    current->next_waiting = waiting;
    waiting = current;
    pthread_mutex_unlock(&mutex);
    suspend();
    pthread_mutex_lock(&mutex);
}

static void run_task(void) {
    current->run(current->work);
    current->returned = true;
}

static void *run_worker(void *context) {
    struct worker *worker = context;
    struct group *group = worker->group;
    bool prefer_incoming = true;
    pthread_mutex_lock(&mutex);
    while (!group->stopping) {
        struct timespec next;
        bool timed = expire_timers(&next);
        struct task *task = prefer_incoming ? dequeue(&group->incoming) : dequeue(&worker->ready);
        if (!task) task = prefer_incoming ? dequeue(&worker->ready) : dequeue(&group->incoming);
        prefer_incoming = !prefer_incoming;
        if (!task) {
            if (timed) pthread_cond_clockwait(&changed, &mutex, CLOCK_MONOTONIC, &next);
            else pthread_cond_wait(&changed, &mutex);
            continue;
        }
        bool started = task->worker != NULL;
        task->worker = worker;
        pthread_mutex_unlock(&mutex);
        if (!started) {
            if (getcontext(&task->context)) abort();
            task->context.uc_stack.ss_sp = (char *)task->mapping + task->guard_size;
            task->context.uc_stack.ss_size = task->mapping_size - task->guard_size;
            task->context.uc_stack.ss_flags = 0;
            task->context.uc_link = &worker->scheduler;
            makecontext(&task->context, run_task, 0);
        }
        current = task;
        if (swapcontext(&worker->scheduler, &task->context)) abort();
        current = NULL;
        if (task->returned) munmap(task->mapping, task->mapping_size);
        pthread_mutex_lock(&mutex);
        if (task->returned) {
            task->finished = true;
            group->pending--;
            awaken_waiters(WAIT_TASK, task);
            if (group->pending == 0) awaken_waiters(WAIT_GROUP, group);
            pthread_cond_broadcast(&changed);
        }
    }
    pthread_mutex_unlock(&mutex);
    return NULL;
}

void *__vqr_rt_group_create(size_t workers, void (*report)(uint32_t)) {
    if (workers == 0 || workers > 256) return NULL;
    struct group *group = calloc(1, sizeof(*group));
    if (!group) return NULL;
    group->workers = calloc(workers, sizeof(*group->workers));
    if (!group->workers) {
        free(group);
        return NULL;
    }
    group->references = 1;
    group->report = report;
    for (; group->worker_count < workers; ++group->worker_count) {
        struct worker *worker = &group->workers[group->worker_count];
        worker->group = group;
        if (pthread_create(&worker->thread, NULL, run_worker, worker)) {
            pthread_mutex_lock(&mutex);
            group->stopping = true;
            pthread_cond_broadcast(&changed);
            pthread_mutex_unlock(&mutex);
            for (size_t i = 0; i < group->worker_count; ++i) pthread_join(group->workers[i].thread, NULL);
            free(group->workers);
            free(group);
            return NULL;
        }
    }
    return group;
}

static void release_group(struct group *group) {
    pthread_mutex_lock(&mutex);
    bool last = --group->references == 0;
    pthread_mutex_unlock(&mutex);
    if (last) {
        free(group->workers);
        free(group);
    }
}

void __vqr_rt_task_release(struct task *task) {
    pthread_mutex_lock(&mutex);
    bool last = --task->references == 0;
    pthread_mutex_unlock(&mutex);
    if (last) {
        struct group *group = task->group;
        if (task->result) task->drop_result(task->result);
        free(task);
        release_group(group);
    }
}

static void cancel_group(struct group *group) {
    group->cancelled = true;
    for (struct task *task = group->tasks; task; task = task->next_group) task->cancelled = true;
    awaken_cancelled();
    pthread_cond_broadcast(&changed);
}

void __vqr_rt_group_cancel(struct group *group) {
    pthread_mutex_lock(&mutex);
    cancel_group(group);
    pthread_mutex_unlock(&mutex);
}

int __vqr_rt_group_cancelled(struct group *group) {
    pthread_mutex_lock(&mutex);
    bool result = group->cancelled;
    pthread_mutex_unlock(&mutex);
    return result;
}

size_t __vqr_rt_group_active(struct group *group) {
    pthread_mutex_lock(&mutex);
    size_t pending = group->pending;
    pthread_mutex_unlock(&mutex);
    return pending;
}

int __vqr_rt_group_wait(struct group *group) {
    if (current && current->group == group) return TASK_SELF;
    pthread_mutex_lock(&mutex);
    while (group->pending) {
        if (current && current->cancelled) {
            pthread_mutex_unlock(&mutex);
            return TASK_INTERRUPTED;
        }
        wait_for(WAIT_GROUP, group, true);
    }
    pthread_mutex_unlock(&mutex);
    return TASK_OK;
}

int __vqr_rt_group_close(struct group *group) {
    if (current && current->group == group) return TASK_SELF;
    pthread_mutex_lock(&mutex);
    cancel_group(group);
    while (group->pending) wait_for(WAIT_GROUP, group, false);
    group->closed = true;
    group->stopping = true;
    struct task *task = group->tasks;
    group->tasks = NULL;
    pthread_cond_broadcast(&changed);
    pthread_mutex_unlock(&mutex);
    for (size_t i = 0; i < group->worker_count; ++i) pthread_join(group->workers[i].thread, NULL);
    while (task) {
        struct task *next = task->next_group;
        if (!task->claimed && task->result) {
            if (task->error) {
                if (group->report) group->report(task->error);
                else fprintf(stderr, "Vaqrin: unobserved task error (%u)\n", task->error);
            }
            task->drop_result(task->result);
            task->result = NULL;
        }
        __vqr_rt_task_release(task);
        task = next;
    }
    release_group(group);
    return TASK_OK;
}

void *__vqr_rt_task_submit(struct group *group, void *work, void (*run)(void *),
                           void *result, void (*drop_result)(void *)) {
    long page = sysconf(_SC_PAGESIZE);
    if (page <= 0) return NULL;
    struct task *task = calloc(1, sizeof(*task));
    if (!task) return NULL;
    task->guard_size = (size_t)page;
    task->mapping_size = task->guard_size + 256 * 1024;
    task->mapping = mmap(NULL, task->mapping_size, PROT_READ | PROT_WRITE,
                         MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (task->mapping == MAP_FAILED || mprotect(task->mapping, task->guard_size, PROT_NONE)) {
        if (task->mapping != MAP_FAILED) munmap(task->mapping, task->mapping_size);
        free(task);
        return NULL;
    }
    task->group = group;
    task->work = work;
    task->run = run;
    task->result = result;
    task->drop_result = drop_result;
    task->references = 2;
    pthread_mutex_lock(&mutex);
    if (group->cancelled || group->stopping) {
        pthread_mutex_unlock(&mutex);
        munmap(task->mapping, task->mapping_size);
        free(task);
        return NULL;
    }
    group->references++;
    task->next_group = group->tasks;
    group->tasks = task;
    group->pending++;
    enqueue(&group->incoming, task);
    pthread_cond_broadcast(&changed);
    pthread_mutex_unlock(&mutex);
    return task;
}

int __vqr_rt_task_join(struct task *task, int block) {
    if (current == task) return TASK_SELF;
    pthread_mutex_lock(&mutex);
    for (;;) {
        enum task_status status;
        if (task->group->closed) status = TASK_CLOSED;
        else if (task->claimed) status = TASK_JOINED;
        else if (task->finished) {
            task->claimed = true;
            status = TASK_OK;
        } else if (!block) status = TASK_RUNNING;
        else if (current && current->cancelled) status = TASK_INTERRUPTED;
        else {
            wait_for(WAIT_TASK, task, true);
            continue;
        }
        pthread_mutex_unlock(&mutex);
        return status;
    }
}

int __vqr_rt_task_finished(struct task *task) {
    pthread_mutex_lock(&mutex);
    bool result = task->finished;
    pthread_mutex_unlock(&mutex);
    return result;
}

void __vqr_rt_task_cancel(struct task *task) {
    pthread_mutex_lock(&mutex);
    task->cancelled = true;
    awaken_cancelled();
    pthread_cond_broadcast(&changed);
    pthread_mutex_unlock(&mutex);
}

void __vqr_rt_task_error(uint32_t error) {
    if (current) current->error = error;
}

void *__vqr_rt_task_current(void) { return current; }

int __vqr_rt_task_cancelled(void) {
    if (!current) return false;
    pthread_mutex_lock(&mutex);
    bool cancelled = current->cancelled;
    pthread_mutex_unlock(&mutex);
    return cancelled;
}

void __vqr_rt_yield(void) {
    if (!current) {
        sched_yield();
        return;
    }
    pthread_mutex_lock(&mutex);
    enqueue(&current->worker->ready, current);
    pthread_mutex_unlock(&mutex);
    suspend();
}

int __vqr_rt_sleep_until(int64_t sec, int64_t nano) {
    struct timespec deadline = { .tv_sec = sec, .tv_nsec = nano };
    if (!current) {
        int status;
        do status = clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &deadline, NULL);
        while (status == EINTR);
        return status == 0;
    }
    pthread_mutex_lock(&mutex);
    if (!current->cancelled) {
        current->deadline = deadline;
        wait_for(WAIT_TIME, NULL, true);
    }
    bool completed = !current->cancelled;
    pthread_mutex_unlock(&mutex);
    return completed;
}
