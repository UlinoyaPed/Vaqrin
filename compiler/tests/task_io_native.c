#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <dirent.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <fcntl.h>
#include <time.h>
#include <unistd.h>

struct group;
struct task;
void *__vqr_rt_group_create(size_t, void (*)(uint32_t));
void *__vqr_rt_task_submit(struct group *, void *, void (*)(void *), void *, void (*)(void *));
void __vqr_rt_task_cancel(struct task *);
int __vqr_rt_task_join(struct task *, int);
int __vqr_rt_group_close(struct group *);
void __vqr_rt_task_release(struct task *);
int __vqr_rt_wait_io(int32_t, uint32_t, int64_t, int64_t);

struct probe {
    int pipe[2];
    int result;
};

static void receive(void *argument) {
    struct probe *probe = argument;
    probe->result = __vqr_rt_wait_io(probe->pipe[0], 1, -1, 0);
    if (probe->result == 0) {
        char byte;
        assert(read(probe->pipe[0], &byte, 1) == 1 && byte == 'x');
    }
}

static void send_byte(void *argument) {
    struct probe *probe = argument;
    assert(write(probe->pipe[1], "x", 1) == 1);
}

static void timeout(void *argument) {
    struct probe *probe = argument;
    struct timespec now;
    assert(clock_gettime(CLOCK_MONOTONIC, &now) == 0);
    now.tv_nsec += 1000000;
    if (now.tv_nsec >= 1000000000) {
        ++now.tv_sec;
        now.tv_nsec -= 1000000000;
    }
    probe->result = __vqr_rt_wait_io(probe->pipe[0], 1, now.tv_sec, now.tv_nsec);
}

static void barrier(void *unused) { (void)unused; }

static void join(struct task *task) {
    assert(task != NULL && __vqr_rt_task_join(task, 1) == 0);
    __vqr_rt_task_release(task);
}

static size_t entries(const char *path) {
    DIR *directory = opendir(path);
    assert(directory != NULL);
    size_t count = 0;
    while (readdir(directory) != NULL) ++count;
    assert(closedir(directory) == 0);
    return count;
}

int main(void) {
    size_t descriptors = entries("/proc/self/fd");
    for (int round = 0; round < 100; ++round) {
        struct probe probe;
        assert(pipe2(probe.pipe, O_NONBLOCK | O_CLOEXEC) == 0);
        struct group *group = __vqr_rt_group_create(1, NULL);
        assert(group != NULL);

        struct task *reader = __vqr_rt_task_submit(group, &probe, receive, NULL, NULL);
        join(__vqr_rt_task_submit(group, &probe, send_byte, NULL, NULL));
        join(reader);
        assert(probe.result == 0);

        join(__vqr_rt_task_submit(group, &probe, timeout, NULL, NULL));
        assert(probe.result == ETIMEDOUT);

        reader = __vqr_rt_task_submit(group, &probe, receive, NULL, NULL);
        assert(reader != NULL);
        join(__vqr_rt_task_submit(group, NULL, barrier, NULL, NULL));
        __vqr_rt_task_cancel(reader);
        join(reader);
        assert(probe.result == ECANCELED);

        assert(__vqr_rt_group_close(group) == 0);
        assert(close(probe.pipe[0]) == 0 && close(probe.pipe[1]) == 0);
    }
    assert(entries("/proc/self/fd") == descriptors);
    assert(entries("/proc/self/task") == 3);
    puts("100 native task/reactor lifecycle rounds and descriptor/thread counts passed");
}
