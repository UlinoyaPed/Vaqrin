#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif

#include <cerrno>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <spawn.h>
#include <sys/wait.h>
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
        close_writer();
    }

    void close_writer() {
        if (output < 0) return;
        close(output);
        output = -1;
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

int spawn_process(const char *arguments, size_t count, const char *directory, const Pipe *pipes,
                  pid_t *pid) {
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
    if (*directory) reason = posix_spawn_file_actions_addchdir_np(&actions, directory);
    if (reason == 0 && pipes) {
        reason = posix_spawn_file_actions_addopen(&actions, STDIN_FILENO, "/dev/null", O_RDONLY, 0);
        if (reason == 0)
            reason = posix_spawn_file_actions_adddup2(&actions, pipes[0].output, STDOUT_FILENO);
        if (reason == 0)
            reason = posix_spawn_file_actions_adddup2(&actions, pipes[1].output, STDERR_FILENO);
    }
    if (reason == 0) {
        reason = __vqr_io_env_lock();
        if (reason == 0) {
            reason = posix_spawnp(pid, argv[0], &actions, nullptr, argv, environ);
            __vqr_io_env_unlock();
        }
    }
    posix_spawn_file_actions_destroy(&actions);
    free(argv);
    return reason;
}

int wait_process(pid_t pid, int *status) {
    const auto result = retry_interrupted([&] { return waitpid(pid, status, 0); });
    return result < 0 ? errno : 0;
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

int collect_process_output(const Pipe *pipes, size_t limit, CapturedOutput *capture) {
    struct pollfd ready[2] = {{pipes[0].input, POLLIN, 0}, {pipes[1].input, POLLIN, 0}};
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

int32_t __vqr_process_run(const char *arguments, size_t count, const char *directory,
                             int32_t *code, int32_t *signal) {
    pid_t pid;
    int reason = spawn_process(arguments, count, directory, nullptr, &pid);
    if (reason != 0) return reason;
    int status;
    reason = wait_process(pid, &status);
    if (reason == 0) describe_wait_status(status, code, signal);
    return reason;
}

uintptr_t __vqr_process_output(const char *arguments, size_t count, const char *directory,
                                  size_t limit, int32_t *out_error) {
    Pipe pipes[2];
    int reason = pipes[0].open();
    if (reason == 0) reason = pipes[1].open();
    if (reason != 0) {
        *out_error = reason;
        return 0;
    }
    auto *capture = static_cast<CapturedOutput *>(calloc(1, sizeof(CapturedOutput)));
    if (!capture) {
        *out_error = errno;
        return 0;
    }

    pid_t pid;
    reason = spawn_process(arguments, count, directory, pipes, &pid);
    if (reason == 0) {
        pipes[0].close_writer();
        pipes[1].close_writer();
        reason = collect_process_output(pipes, limit, capture);
        if (reason != 0) kill(pid, SIGKILL);
        const int wait_error = wait_process(pid, &capture->wait_status);
        if (reason == 0) reason = wait_error;
    }
    *out_error = reason;
    if (reason != 0) {
        free_captured_output(capture);
        return 0;
    }
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
