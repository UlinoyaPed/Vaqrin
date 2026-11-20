#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif

#include <cerrno>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <spawn.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

extern "C" int __vqr_io_env_lock();
extern "C" void __vqr_io_env_unlock();

namespace {

template <class Operation>
auto retry_interrupted(Operation operation) {
    auto result = operation();
    while (result < 0 && errno == EINTR) result = operation();
    return result;
}

struct Pipe {
    int input = -1;
    int output = -1;

    ~Pipe() {
        if (input >= 0) close(input);
        if (output >= 0) close(output);
    }

    int open() {
        int fds[2];
        if (pipe2(fds, O_CLOEXEC) != 0) return errno;
        input = fds[0];
        output = fds[1];
        int *const descriptors[] = {&input, &output};
        for (int *fd : descriptors) {
            if (*fd > STDERR_FILENO) continue;
            int duplicate = fcntl(*fd, F_DUPFD_CLOEXEC, STDERR_FILENO + 1);
            if (duplicate < 0) return errno;
            close(*fd);
            *fd = duplicate;
        }
        return 0;
    }
};

struct CapturedBytes {
    uint8_t *data;
    size_t length;
    size_t capacity;
};

struct CapturedOutput {
    CapturedBytes streams[2];
    int wait_status;
};

void free_captured_output(CapturedOutput *capture) {
    free(capture->streams[0].data);
    free(capture->streams[1].data);
    free(capture);
}

enum StreamMode { Null = -2, Piped = -3 };

struct Child {
    pid_t pid;
    int streams[3];
    int wait_status;
    bool waited;
    bool unavailable;
};

void close_child_stream(Child &child, int stream) {
    if (child.streams[stream] >= 0) close(child.streams[stream]);
    child.streams[stream] = -1;
}

bool same_environment_key(const char *entry, const char *key, size_t size) {
    return strncmp(entry, key, size) == 0 && entry[size] == '=';
}

// The caller holds the environment mutex while the array borrows inherited entries.
int make_environment(const char *overrides, size_t count, bool inherit, char ***result) {
    size_t inherited = 0;
    if (inherit && environ)
        while (environ[inherited]) ++inherited;
    if (count >= SIZE_MAX - inherited) return EOVERFLOW;
    auto **entries = static_cast<char **>(calloc(inherited + count + 1, sizeof(char *)));
    if (!entries) return errno;
    if (inherited != 0) memcpy(entries, environ, inherited * sizeof(char *));
    size_t length = inherited;
    auto *entry = const_cast<char *>(overrides);
    for (size_t index = 0; index < count; ++index) {
        const char *equals = strchr(entry, '=');
        const size_t key_length = equals ? static_cast<size_t>(equals - entry) : strlen(entry);
        size_t write_index = 0;
        for (size_t read_index = 0; read_index < length; ++read_index) {
            if (!same_environment_key(entries[read_index], entry, key_length))
                entries[write_index++] = entries[read_index];
        }
        length = write_index;
        if (equals) entries[length++] = entry;
        entry += strlen(entry) + 1;
    }
    entries[length] = nullptr;
    *result = entries;
    return 0;
}

int spawn_process(const char *arguments, size_t count, const char *directory, const int32_t *modes,
                  const char *overrides, size_t override_count, bool inherit_env, Child &child) {
    if (count == 0 || count == SIZE_MAX) return EINVAL;
    auto **argv = static_cast<char **>(calloc(count + 1, sizeof(char *)));
    if (!argv) return errno;
    auto *word = const_cast<char *>(arguments);
    for (size_t index = 0; index < count; ++index) {
        argv[index] = word;
        word += strlen(word) + 1;
    }

    posix_spawn_file_actions_t actions;
    int reason = posix_spawn_file_actions_init(&actions);
    if (reason != 0) {
        free(argv);
        return reason;
    }
    Pipe pipes[3];
    int redirected[3] = {-1, -1, -1};
    if (*directory) reason = posix_spawn_file_actions_addchdir_np(&actions, directory);
    for (int stream = 0; reason == 0 && stream < 3; ++stream) {
        if (modes[stream] == Null) {
            reason = posix_spawn_file_actions_addopen(
                &actions, stream, "/dev/null", stream == STDIN_FILENO ? O_RDONLY : O_WRONLY, 0);
        } else if (modes[stream] == Piped) {
            auto &pipe = pipes[stream];
            reason = pipe.open();
            if (reason == 0) {
                const int fd = stream == STDIN_FILENO ? pipe.input : pipe.output;
                reason = posix_spawn_file_actions_adddup2(&actions, fd, stream);
            }
        } else if (modes[stream] >= 0) {
            const int fd = retry_interrupted([&] {
                return fcntl(modes[stream], F_DUPFD_CLOEXEC, STDERR_FILENO + 1);
            });
            if (fd < 0) {
                reason = errno;
            } else {
                redirected[stream] = fd;
                reason = posix_spawn_file_actions_adddup2(&actions, fd, stream);
            }
        }
    }
    if (reason == 0) {
        reason = __vqr_io_env_lock();
        if (reason == 0) {
            char **entries = nullptr;
            if (override_count != 0 || !inherit_env)
                reason = make_environment(overrides, override_count, inherit_env, &entries);
            if (reason == 0)
                reason = posix_spawnp(&child.pid, argv[0], &actions, nullptr, argv, entries ? entries : environ);
            free(entries);
            __vqr_io_env_unlock();
        }
    }
    posix_spawn_file_actions_destroy(&actions);
    for (int fd : redirected) {
        if (fd >= 0) close(fd);
    }
    free(argv);
    if (reason == 0) {
        for (int stream = 0; stream < 3; ++stream) {
            int &fd = stream == STDIN_FILENO ? pipes[stream].output : pipes[stream].input;
            child.streams[stream] = fd;
            fd = -1;
        }
    }
    return reason;
}

int wait_child(Child &child, bool nonblocking) {
    if (child.waited) return 0;
    if (child.unavailable) return ECHILD;
    for (;;) {
        int status;
        const auto pid = retry_interrupted([&] {
            return waitpid(child.pid, &status, nonblocking ? WNOHANG : 0);
        });
        if (pid < 0) {
            if (errno == ECHILD) child.unavailable = true;
            return errno;
        }
        if (pid == 0) return 0;
        if (!WIFEXITED(status) && !WIFSIGNALED(status)) continue;
        child.wait_status = status;
        child.waited = true;
        return 0;
    }
}

void *reap_detached_child(void *argument) {
    const auto pid = static_cast<pid_t>(reinterpret_cast<uintptr_t>(argument));
    Child child{pid, {-1, -1, -1}, 0, false, false};
    wait_child(child, false);
    return nullptr;
}

void release_child(Child *child) {
    for (int stream = 0; stream < 3; ++stream) close_child_stream(*child, stream);
    if (wait_child(*child, true) == 0 && !child->waited) {
        pthread_attr_t attr;
        int reason = pthread_attr_init(&attr);
        if (reason == 0) {
            reason = pthread_attr_setdetachstate(&attr, PTHREAD_CREATE_DETACHED);
            if (reason == 0) {
                pthread_t worker;
                auto *argument = reinterpret_cast<void *>(static_cast<uintptr_t>(child->pid));
                reason = pthread_create(&worker, &attr, reap_detached_child, argument);
            }
            pthread_attr_destroy(&attr);
        }
        if (reason != 0) wait_child(*child, false);
    }
    free(child);
}

ssize_t write_without_sigpipe(int fd, const void *data, size_t length, int32_t *reason) {
    sigset_t set, previous, pending;
    sigemptyset(&set);
    sigaddset(&set, SIGPIPE);
    *reason = pthread_sigmask(SIG_BLOCK, &set, &previous);
    if (*reason != 0) return -1;
    const int pending_result = sigpending(&pending);
    if (pending_result < 0) {
        *reason = errno;
        pthread_sigmask(SIG_SETMASK, &previous, nullptr);
        return -1;
    }
    const auto written = retry_interrupted([&] { return write(fd, data, length); });
    *reason = written < 0 ? errno : 0;
    if (*reason == EPIPE && sigismember(&pending, SIGPIPE) == 0) {
        struct timespec now{};
        retry_interrupted([&] { return sigtimedwait(&set, nullptr, &now); });
    }
    pthread_sigmask(SIG_SETMASK, &previous, nullptr);
    return written;
}

int append_captured_bytes(CapturedBytes &stream, const uint8_t *data, size_t length, size_t limit) {
    const size_t needed = stream.length + length;
    if (needed > stream.capacity) {
        const size_t spare = limit - stream.capacity;
        const size_t grown = stream.capacity + (stream.capacity < spare ? stream.capacity : spare);
        const size_t capacity = needed > grown ? needed : grown;
        auto *replacement = static_cast<uint8_t *>(realloc(stream.data, capacity));
        if (!replacement) return errno;
        stream.data = replacement;
        stream.capacity = capacity;
    }
    memcpy(stream.data + stream.length, data, length);
    stream.length = needed;
    return 0;
}

constexpr int OUTPUT_LIMIT = -1;

int collect_process_output(const Child &child, size_t limit, CapturedOutput *capture) {
    struct pollfd ready[2] = {{child.streams[1], POLLIN, 0}, {child.streams[2], POLLIN, 0}};
    size_t total = 0;
    while (ready[0].fd >= 0 || ready[1].fd >= 0) {
        if (retry_interrupted([&] { return poll(ready, 2, -1); }) < 0) return errno;
        for (size_t index = 0; index < 2; ++index) {
            if (ready[index].revents == 0) continue;
            uint8_t buffer[8192];
            const auto received = retry_interrupted([&] { return read(ready[index].fd, buffer, sizeof(buffer)); });
            if (received < 0) return errno;
            if (received == 0) {
                ready[index].fd = -1;
                continue;
            }
            if (!capture) continue;
            const size_t length = static_cast<size_t>(received);
            if (length > limit - total) return OUTPUT_LIMIT;
            const int reason = append_captured_bytes(capture->streams[index], buffer, length, limit);
            if (reason != 0) return reason;
            total += length;
        }
    }
    return 0;
}

void describe_wait_status(int status, int32_t *code, int32_t *signal) {
    *code = WIFEXITED(status) ? WEXITSTATUS(status) : -1;
    *signal = WIFSIGNALED(status) ? WTERMSIG(status) : 0;
}

} // namespace

extern "C" {

uintptr_t __vqr_process_spawn(const char *arguments, size_t count, const char *directory,
                              const int32_t *modes, const char *overrides, size_t override_count,
                              int32_t inherit_env, int32_t *out_error) {
    auto *child = static_cast<Child *>(malloc(sizeof(Child)));
    if (!child) {
        *out_error = errno;
        return 0;
    }
    *child = Child{0, {-1, -1, -1}, 0, false, false};
    *out_error = spawn_process(arguments, count, directory, modes, overrides, override_count,
                              inherit_env != 0, *child);
    if (*out_error != 0) {
        free(child);
        return 0;
    }
    return reinterpret_cast<uintptr_t>(child);
}

void __vqr_process_child_free(uintptr_t address) {
    release_child(reinterpret_cast<Child *>(address));
}

uint64_t __vqr_process_child_id(uintptr_t address) {
    return reinterpret_cast<Child *>(address)->pid;
}

int32_t __vqr_process_child_has_pipe(uintptr_t address, int32_t stream) {
    return reinterpret_cast<Child *>(address)->streams[stream] >= 0;
}

void __vqr_process_child_close(uintptr_t address, int32_t stream) {
    close_child_stream(*reinterpret_cast<Child *>(address), stream);
}

intptr_t __vqr_process_child_read(uintptr_t address, int32_t stream, void *data, size_t length,
                                 int32_t *out_error) {
    const int fd = reinterpret_cast<Child *>(address)->streams[stream];
    auto result = retry_interrupted([&] { return read(fd, data, length); });
    *out_error = result < 0 ? errno : 0;
    return result;
}

intptr_t __vqr_process_child_write(uintptr_t address, const void *data, size_t length,
                                  int32_t *out_error) {
    const int fd = reinterpret_cast<Child *>(address)->streams[0];
    return write_without_sigpipe(fd, data, length, out_error);
}

int32_t __vqr_process_child_wait(uintptr_t address, int32_t mode, int32_t *ready,
                                 int32_t *code, int32_t *signal) {
    auto &child = *reinterpret_cast<Child *>(address);
    const bool nonblocking = mode == 1;
    if (!nonblocking) close_child_stream(child, 0);
    if (mode == 2) {
        const int reason = collect_process_output(child, 0, nullptr);
        close_child_stream(child, 1);
        close_child_stream(child, 2);
        if (reason != 0) return reason;
    }
    const int reason = wait_child(child, nonblocking);
    if (reason != 0) return reason;
    *ready = child.waited;
    if (child.waited) describe_wait_status(child.wait_status, code, signal);
    return 0;
}

int32_t __vqr_process_child_signal(uintptr_t address, int32_t force) {
    auto &child = *reinterpret_cast<Child *>(address);
    const int reason = wait_child(child, true);
    if (reason != 0) return reason;
    if (child.waited) return ESRCH;
    return kill(child.pid, force != 0 ? SIGKILL : SIGTERM) == 0 ? 0 : errno;
}

uintptr_t __vqr_process_child_output(uintptr_t address, size_t limit, int32_t *out_error) {
    auto &child = *reinterpret_cast<Child *>(address);
    auto *capture = static_cast<CapturedOutput *>(calloc(1, sizeof(CapturedOutput)));
    if (!capture) {
        *out_error = errno;
        return 0;
    }

    close_child_stream(child, 0);
    int reason = collect_process_output(child, limit, capture);
    close_child_stream(child, 1);
    close_child_stream(child, 2);
    if (reason != 0 && wait_child(child, true) == 0 && !child.waited) kill(child.pid, SIGKILL);
    const int wait_error = wait_child(child, false);
    if (reason == 0) reason = wait_error;
    *out_error = reason;
    if (reason != 0) {
        free_captured_output(capture);
        return 0;
    }
    capture->wait_status = child.wait_status;
    return reinterpret_cast<uintptr_t>(capture);
}

void __vqr_process_output_status(uintptr_t address, int32_t *code, int32_t *signal) {
    auto *capture = reinterpret_cast<CapturedOutput *>(address);
    describe_wait_status(capture->wait_status, code, signal);
}

uintptr_t __vqr_process_output_bytes(uintptr_t address, int32_t stream, size_t *length) {
    auto *capture = reinterpret_cast<CapturedOutput *>(address);
    const auto &buffer = capture->streams[stream];
    *length = buffer.length;
    return reinterpret_cast<uintptr_t>(buffer.data);
}

void __vqr_process_output_free(uintptr_t address) {
    free_captured_output(reinterpret_cast<CapturedOutput *>(address));
}

uint64_t __vqr_process_pid() { return getpid(); }
uint64_t __vqr_process_parent_pid() { return getppid(); }

} // extern "C"
