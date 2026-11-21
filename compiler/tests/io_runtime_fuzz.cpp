#include <cassert>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <sys/stat.h>
#include <pthread.h>
#include <signal.h>
#include <sys/socket.h>
#include <unistd.h>

extern "C" {
int32_t __vqr_io_reactor_open(int32_t *);
int32_t __vqr_io_reactor_add(int32_t, int32_t, uint64_t, uint32_t, int32_t *);
int32_t __vqr_io_reactor_update(int32_t, int32_t, uint64_t, uint32_t);
int32_t __vqr_io_reactor_remove(int32_t, int32_t);
int32_t __vqr_io_reactor_wait(int32_t, int32_t, uint64_t *, uint32_t *, int32_t *);
void __vqr_io_reactor_close(int32_t);
int32_t __vqr_io_temp_at(int32_t, uint8_t *, int32_t, int32_t *);
int32_t __vqr_io_rename_at(int32_t, const char *, const char *);
int32_t __vqr_io_sync_dir(int32_t);
int32_t __vqr_io_remove_tree_at(int32_t, const char *, uint32_t);
intptr_t __vqr_io_file_read_at(int32_t, uint8_t *, size_t, uint64_t, int32_t *);
intptr_t __vqr_io_file_write_at(int32_t, const uint8_t *, size_t, uint64_t, int32_t *);
int32_t __vqr_io_file_truncate(int32_t, uint64_t, int32_t *);
int32_t __vqr_io_file_metadata(int32_t, uint64_t *, uint32_t *, int64_t *, int32_t *,
                               uint64_t *, uint64_t *, int32_t *);
}

namespace {

void exercise_file(const uint8_t *data, size_t size) {
    char filename[] = "/tmp/vaqrin-native-XXXXXX";
    const int fd = mkstemp(filename);
    assert(fd >= 0);
    assert(unlink(filename) == 0);
    uint8_t out[256]{};
    if (size > sizeof(out)) size = sizeof(out);
    const auto offset = static_cast<uint64_t>(data[0]);
    int32_t status = 0;
    assert(__vqr_io_file_write_at(fd, data, size, offset, &status) == static_cast<intptr_t>(size));
    assert(status == 0);
    assert(__vqr_io_file_read_at(fd, out, size, offset, &status) == static_cast<intptr_t>(size));
    assert(status == 0 && memcmp(out, data, size) == 0);
    const uint64_t final_size = data[size - 1];
    assert(__vqr_io_file_truncate(fd, final_size, &status) == 0);
    uint64_t actual = 0, device = 0, inode = 0;
    int32_t kind = -1;
    assert(__vqr_io_file_metadata(fd, &actual, nullptr, nullptr, &kind, &device, &inode, &status) == 0);
    assert(actual == final_size && kind == 0 && inode != 0);
    assert(__vqr_io_file_read_at(fd, out, 1, UINT64_MAX, &status) < 0);
    assert(status != 0);
    assert(close(fd) == 0);
}

void exercise_reactor(const uint8_t *data, size_t size) {
    int peer[2];
    int32_t reason = 0;
    assert(socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0, peer) == 0);
    int epoll = __vqr_io_reactor_open(&reason);
    assert(epoll >= 0 && reason == 0);
    uint64_t token = 1;
    int watch = __vqr_io_reactor_add(epoll, peer[0], token, 1, &reason);
    assert(watch >= 0);
    for (size_t index = 0; index < size; ++index) {
        switch (data[index] & 3) {
        case 0: {
            const ssize_t sent = send(peer[1], data + index, 1, MSG_NOSIGNAL);
            assert(sent == 1 || errno == EAGAIN);
            break;
        }
        case 1: {
            uint8_t item;
            const ssize_t got = recv(peer[0], &item, 1, 0);
            assert(got == 1 || errno == EAGAIN);
            break;
        }
        case 2:
            assert(__vqr_io_reactor_update(epoll, watch, token, 1 + (data[index] >> 2) % 3) == 0);
            break;
        default: {
            assert(__vqr_io_reactor_remove(epoll, watch) == 0);
            __vqr_io_reactor_close(watch);
            uint64_t observed = 0;
            uint32_t readiness = 0;
            assert(__vqr_io_reactor_wait(epoll, 0, &observed, &readiness, &reason) == 0);
            watch = __vqr_io_reactor_add(epoll, peer[0], ++token, 1, &reason);
            assert(watch >= 0);
            break;
        }
        }
        uint64_t observed = 0;
        uint32_t readiness = 0;
        const int ready = __vqr_io_reactor_wait(epoll, 0, &observed, &readiness, &reason);
        assert(ready >= 0);
        if (ready == 1) assert(observed == token && readiness != 0);
    }
    assert(__vqr_io_reactor_remove(epoll, watch) == 0);
    __vqr_io_reactor_close(watch);
    __vqr_io_reactor_close(epoll);
    assert(close(peer[0]) == 0 && close(peer[1]) == 0);
}

#ifdef VAQRIN_STANDALONE_FUZZ
void test_directory_resources() {
    char directory[] = "/tmp/vaqrin-dir-XXXXXX";
    assert(mkdtemp(directory) != nullptr);
    int parent = open(directory, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    assert(parent >= 0);
    uint8_t name[25]{};
    int32_t reason = 0;
    int file = __vqr_io_temp_at(parent, name, 0, &reason);
    assert(file >= 0 && reason == 0);
    assert(write(file, "data", 4) == 4 && fsync(file) == 0 && close(file) == 0);
    assert(__vqr_io_rename_at(parent, reinterpret_cast<const char *>(name), "published") == 0);
    assert(__vqr_io_sync_dir(parent) == 0);
    assert(__vqr_io_temp_at(parent, name, 1, &reason) == 0 && reason == 0);
    const char *child = reinterpret_cast<const char *>(name);
    int nested = openat(parent, child, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    assert(nested >= 0);
    assert(symlinkat("..", nested, "cycle") == 0);
    assert(__vqr_io_remove_tree_at(parent, child, 0) == ELOOP);
    assert(close(nested) == 0);
    assert(__vqr_io_remove_tree_at(parent, child, 5) == 0);
    assert(__vqr_io_remove_tree_at(parent, "published", 5) == 0);
    assert(close(parent) == 0 && rmdir(directory) == 0);
}

void handle_signal(int) {}

void test_interrupt() {
    struct sigaction action{};
    action.sa_handler = handle_signal;
    sigemptyset(&action.sa_mask);
    assert(sigaction(SIGUSR1, &action, nullptr) == 0);
    pthread_t target = pthread_self();
    pthread_t interrupter;
    assert(pthread_create(&interrupter, nullptr, [](void *ptr) -> void * {
        // Repeat the signal to cover both scheduling orders at entry to epoll_wait.
        for (int attempt = 0; attempt < 100; ++attempt) {
            usleep(1000);
            assert(pthread_kill(*static_cast<pthread_t *>(ptr), SIGUSR1) == 0);
        }
        return nullptr;
    }, &target) == 0);
    int32_t reason = 0;
    const int epoll = __vqr_io_reactor_open(&reason);
    uint64_t token = 0;
    uint32_t readiness = 0;
    assert(__vqr_io_reactor_wait(epoll, 1000, &token, &readiness, &reason) == -1);
    assert(reason == EINTR);
    while (pthread_join(interrupter, nullptr) == EINTR) {}
    __vqr_io_reactor_close(epoll);
}
#endif
} // namespace

extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size == 0) return 0;
    if (size > 256) size = 256;
    exercise_file(data, size);
    exercise_reactor(data, size);
    return 0;
}

#ifdef VAQRIN_STANDALONE_FUZZ
int main() {
    test_interrupt();
    test_directory_resources();
    uint32_t state = 0x39a4fca1;
    uint8_t data[256];
    for (unsigned iteration = 0; iteration < 5000; ++iteration) {
        for (auto &byte : data) {
            state = state * 1664525u + 1013904223u;
            byte = static_cast<uint8_t>(state >> 24);
        }
        LLVMFuzzerTestOneInput(data, 1 + state % sizeof(data));
    }
    std::puts("5000 deterministic file/epoll fuzz cases, directory cleanup and kernel EINTR passed");
}
#endif
