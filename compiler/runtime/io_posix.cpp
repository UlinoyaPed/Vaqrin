#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#define _DEFAULT_SOURCE
#define _FILE_OFFSET_BITS 64

#include "reactor_linux.h"
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <locale.h>
#include <pthread.h>
#include <stdlib.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

static void set_error(int32_t *out_error, int value) {
    if (out_error) *out_error = value;
}

template <class Operation>
static auto retry_interrupted(Operation operation) {
    auto result = operation();
    while (result < 0 && errno == EINTR) result = operation();
    return result;
}

static int file_kind(mode_t mode) {
    if (S_ISREG(mode)) return 0;
    if (S_ISDIR(mode)) return 1;
    if (S_ISLNK(mode)) return 2;
    if (S_ISCHR(mode) || S_ISBLK(mode) || S_ISFIFO(mode)) return 3;
    if (S_ISSOCK(mode)) return 4;
    return 5;
}

static void write_metadata(const struct stat *value, uint64_t *size, uint32_t *mode,
                           int64_t *modified, int32_t *kind,
                           uint64_t *device, uint64_t *inode) {
    if (size) *size = value->st_size < 0 ? 0u : (uint64_t)value->st_size;
    if (mode) *mode = (uint32_t)(value->st_mode & 07777);
    if (modified) *modified = (int64_t)value->st_mtim.tv_sec;
    if (kind) *kind = (int32_t)file_kind(value->st_mode);
    if (device) *device = static_cast<uint64_t>(value->st_dev);
    if (inode) *inode = static_cast<uint64_t>(value->st_ino);
}

namespace {

pthread_mutex_t environment_mutex = PTHREAD_MUTEX_INITIALIZER;

struct EnvironmentLock {
    const int status = pthread_mutex_lock(&environment_mutex);
    ~EnvironmentLock() {
        if (status == 0) pthread_mutex_unlock(&environment_mutex);
    }
};

void free_environment(char **entries, size_t count) {
    for (size_t index = 0; index < count; ++index) free(entries[index]);
    free(entries);
}

} // namespace

extern "C" {

int32_t __vqr_io_reactor_open(int32_t *out_error) {
    const int fd = epoll_create1(EPOLL_CLOEXEC);
    set_error(out_error, fd < 0 ? errno : 0);
    return fd;
}

int32_t __vqr_io_reactor_add(int32_t epoll, int32_t fd, uint64_t token, uint32_t interests,
                            int32_t *out_error) {
    const int watched = retry_interrupted([&] { return fcntl(fd, F_DUPFD_CLOEXEC, 0); });
    if (watched < 0) {
        set_error(out_error, errno);
        return -1;
    }
    if (vqr_epoll_set(epoll, EPOLL_CTL_ADD, watched, token, interests, 0) != 0) {
        const int reason = errno;
        close(watched);
        set_error(out_error, reason);
        return -1;
    }
    set_error(out_error, 0);
    return watched;
}

int32_t __vqr_io_reactor_update(int32_t epoll, int32_t fd, uint64_t token, uint32_t interests) {
    return vqr_epoll_set(epoll, EPOLL_CTL_MOD, fd, token, interests, 0) == 0 ? 0 : errno;
}

int32_t __vqr_io_reactor_remove(int32_t epoll, int32_t fd) {
    return epoll_ctl(epoll, EPOLL_CTL_DEL, fd, nullptr) == 0 ? 0 : errno;
}

void __vqr_io_reactor_close(int32_t fd) { close(fd); }

int32_t __vqr_io_reactor_wait(int32_t epoll, int32_t millis, uint64_t *token,
                             uint32_t *ready, int32_t *out_error) {
    struct epoll_event event{};
    const int count = epoll_wait(epoll, &event, 1, millis);
    set_error(out_error, count < 0 ? errno : 0);
    if (count > 0) {
        *token = event.data.u64;
        *ready = vqr_epoll_ready(event.events);
    }
    return count;
}

int32_t __vqr_io_open(const char *path, uint32_t flags, uint32_t mode, int32_t *out_error) {
    int native_flags = O_CLOEXEC;
    int reading = (flags & 1u) != 0;
    int writing = (flags & 2u) != 0;
    if (reading && writing) native_flags |= O_RDWR;
    else if (writing) native_flags |= O_WRONLY;
    else native_flags |= O_RDONLY;
    if (flags & 4u) native_flags |= O_APPEND;
    if (flags & 8u) native_flags |= O_CREAT;
    if (flags & 16u) native_flags |= O_CREAT | O_EXCL;
    if (flags & 32u) native_flags |= O_TRUNC;
    int descriptor = retry_interrupted([&] { return open(path, native_flags, (mode_t)mode); });
    if (descriptor < 0) {
        set_error(out_error, errno);
        return -1;
    }
    set_error(out_error, 0);
    return descriptor;
}

intptr_t __vqr_io_file_read(int32_t descriptor, uint8_t *dst, size_t length, int32_t *out_error) {
    if (length == 0) {
        set_error(out_error, 0);
        return 0;
    }
    ssize_t count = retry_interrupted([&] { return read(descriptor, dst, length); });
    if (count < 0) {
        set_error(out_error, errno);
        return -1;
    }
    set_error(out_error, 0);
    return (intptr_t)count;
}

intptr_t __vqr_io_file_write(int32_t descriptor, const uint8_t *src, size_t length, int32_t *out_error) {
    if (length == 0) {
        set_error(out_error, 0);
        return 0;
    }
    ssize_t count = retry_interrupted([&] { return write(descriptor, src, length); });
    if (count < 0) {
        set_error(out_error, errno);
        return -1;
    }
    set_error(out_error, 0);
    return (intptr_t)count;
}

intptr_t __vqr_io_file_read_at(int32_t descriptor, uint8_t *dst, size_t length,
                                  uint64_t offset, int32_t *out_error) {
    if (offset > (uint64_t)INT64_MAX) {
        set_error(out_error, EOVERFLOW);
        return -1;
    }
    if (length == 0) {
        set_error(out_error, 0);
        return 0;
    }
    ssize_t count = retry_interrupted([&] { return pread(descriptor, dst, length, (off_t)offset); });
    if (count < 0) {
        set_error(out_error, errno);
        return -1;
    }
    set_error(out_error, 0);
    return (intptr_t)count;
}

intptr_t __vqr_io_file_write_at(int32_t descriptor, const uint8_t *src, size_t length,
                                 uint64_t offset, int32_t *out_error) {
    if (offset > (uint64_t)INT64_MAX) {
        set_error(out_error, EOVERFLOW);
        return -1;
    }
    if (length == 0) {
        set_error(out_error, 0);
        return 0;
    }
    ssize_t count = retry_interrupted([&] { return pwrite(descriptor, src, length, (off_t)offset); });
    if (count < 0) {
        set_error(out_error, errno);
        return -1;
    }
    set_error(out_error, 0);
    return (intptr_t)count;
}

int64_t __vqr_io_file_seek(int32_t descriptor, int64_t offset, int32_t origin, int32_t *out_error) {
    int whence = origin == 0 ? SEEK_SET : origin == 1 ? SEEK_CUR : origin == 2 ? SEEK_END : -1;
    if (whence < 0) {
        set_error(out_error, EINVAL);
        return -1;
    }
    off_t result = lseek(descriptor, (off_t)offset, whence);
    if (result < 0) {
        set_error(out_error, errno);
        return -1;
    }
    set_error(out_error, 0);
    return (int64_t)result;
}

int32_t __vqr_io_file_close(int32_t descriptor, int32_t *out_error) {
    if (close(descriptor) != 0) {
        set_error(out_error, errno);
        return -1;
    }
    set_error(out_error, 0);
    return 0;
}

int32_t __vqr_io_file_sync(int32_t descriptor, int32_t data_only, int32_t *out_error) {
    int result = data_only ? fdatasync(descriptor) : fsync(descriptor);
    if (result != 0) {
        set_error(out_error, errno);
        return -1;
    }
    set_error(out_error, 0);
    return 0;
}

int32_t __vqr_io_file_truncate(int32_t descriptor, uint64_t size, int32_t *out_error) {
    if (size > (uint64_t)INT64_MAX) {
        set_error(out_error, EOVERFLOW);
        return -1;
    }
    if (ftruncate(descriptor, (off_t)size) != 0) {
        set_error(out_error, errno);
        return -1;
    }
    set_error(out_error, 0);
    return 0;
}

int32_t __vqr_io_file_metadata(int32_t descriptor, uint64_t *size, uint32_t *mode,
                               int64_t *modified, int32_t *kind, uint64_t *device,
                               uint64_t *inode, int32_t *out_error) {
    struct stat value;
    if (fstat(descriptor, &value) != 0) {
        set_error(out_error, errno);
        return -1;
    }
    write_metadata(&value, size, mode, modified, kind, device, inode);
    set_error(out_error, 0);
    return 0;
}

int32_t __vqr_io_file_set_mode(int32_t descriptor, uint32_t mode, int32_t *out_error) {
    if (fchmod(descriptor, (mode_t)(mode & 07777u)) != 0) {
        set_error(out_error, errno);
        return -1;
    }
    set_error(out_error, 0);
    return 0;
}

int32_t __vqr_io_stat(const char *path, int32_t follow, uint64_t *size, uint32_t *mode,
                      int64_t *modified, int32_t *kind, uint64_t *device,
                               uint64_t *inode, int32_t *out_error) {
    struct stat value;
    int result = follow ? stat(path, &value) : lstat(path, &value);
    if (result != 0) {
        set_error(out_error, errno);
        return -1;
    }
    write_metadata(&value, size, mode, modified, kind, device, inode);
    set_error(out_error, 0);
    return 0;
}

int32_t __vqr_io_mkdir(const char *path, uint32_t mode, int32_t *out_error) {
    if (mkdir(path, (mode_t)(mode & 07777u)) != 0) {
        set_error(out_error, errno);
        return -1;
    }
    set_error(out_error, 0);
    return 0;
}

int32_t __vqr_io_remove(const char *path, int32_t *out_error) {
    if (unlink(path) != 0) {
        set_error(out_error, errno);
        return -1;
    }
    set_error(out_error, 0);
    return 0;
}

int32_t __vqr_io_remove_dir(const char *path, int32_t *out_error) {
    if (rmdir(path) != 0) {
        set_error(out_error, errno);
        return -1;
    }
    set_error(out_error, 0);
    return 0;
}

int32_t __vqr_io_rename(const char *from, const char *to, int32_t *out_error) {
    if (rename(from, to) != 0) {
        set_error(out_error, errno);
        return -1;
    }
    set_error(out_error, 0);
    return 0;
}

int32_t __vqr_io_chmod(const char *path, uint32_t mode, int32_t *out_error) {
    if (chmod(path, (mode_t)(mode & 07777u)) != 0) {
        set_error(out_error, errno);
        return -1;
    }
    set_error(out_error, 0);
    return 0;
}

int32_t __vqr_io_symlink(const char *target, const char *name) {
    return symlink(target, name) == 0 ? 0 : errno;
}

intptr_t __vqr_io_readlink(const char *name, uint8_t *out, size_t capacity, int32_t *reason) {
    const ssize_t size = retry_interrupted([&] { return readlink(name, reinterpret_cast<char *>(out), capacity); });
    set_error(reason, size < 0 ? errno : 0);
    return size;
}

static DIR *directory_at(int parent, const char *name, bool nofollow) {
    int fd = retry_interrupted([&] {
        return openat(parent, name, O_RDONLY | O_CLOEXEC | O_DIRECTORY | (nofollow ? O_NOFOLLOW : 0));
    });
    if (fd < 0) return nullptr;
    DIR *directory = fdopendir(fd);
    if (!directory) {
        const int reason = errno;
        close(fd);
        errno = reason;
    }
    return directory;
}

uintptr_t __vqr_io_dir_open_at(int32_t parent, const char *name, int32_t *reason) {
    DIR *directory = directory_at(parent, name, true);
    set_error(reason, directory ? 0 : errno);
    return reinterpret_cast<uintptr_t>(directory);
}

uintptr_t __vqr_io_dir_open_nofollow(const char *name, int32_t *reason) {
    return __vqr_io_dir_open_at(AT_FDCWD, name, reason);
}

int32_t __vqr_io_dir_fd(uintptr_t directory) {
    return dirfd(reinterpret_cast<DIR *>(directory));
}

int32_t __vqr_io_unlink_at(int32_t directory, const char *name, int32_t is_directory) {
    return unlinkat(directory, name, is_directory ? AT_REMOVEDIR : 0) == 0 ? 0 : errno;
}

int32_t __vqr_io_rename_at(int32_t directory, const char *from, const char *to) {
    return renameat(directory, from, directory, to) == 0 ? 0 : errno;
}

int32_t __vqr_io_sync_dir(int32_t directory) {
    return retry_interrupted([&] { return fsync(directory); }) == 0 ? 0 : errno;
}

int32_t __vqr_io_temp_at(int32_t directory, uint8_t *name, int32_t create_directory, int32_t *reason) {
    constexpr char prefix[] = ".vaqrin-";
    constexpr char digits[] = "0123456789abcdef";
    memcpy(name, prefix, sizeof(prefix) - 1);
    for (int retry = 0; retry < 64; ++retry) {
        uint8_t nonce[8];
        const ssize_t count = retry_interrupted([&] { return getrandom(nonce, sizeof(nonce), 0); });
        if (count != sizeof(nonce)) {
            set_error(reason, count < 0 ? errno : EIO);
            return -1;
        }
        for (size_t index = 0; index < sizeof(nonce); ++index) {
            name[8 + 2 * index] = digits[nonce[index] >> 4];
            name[9 + 2 * index] = digits[nonce[index] & 15];
        }
        name[24] = '\0';
        const char *path = reinterpret_cast<const char *>(name);
        const int result = create_directory ? mkdirat(directory, path, 0700)
            : openat(directory, path, O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC | O_RDWR, 0600);
        if (result >= 0) {
            set_error(reason, 0);
            return result;
        }
        if (errno != EEXIST) {
            set_error(reason, errno);
            return -1;
        }
    }
    set_error(reason, EEXIST);
    return -1;
}

static bool dot_entry(const char *name) {
    return name[0] == '.' && (name[1] == '\0' || (name[1] == '.' && name[2] == '\0'));
}

int32_t __vqr_io_remove_tree_at(int32_t parent, const char *name, uint32_t depth) {
    struct stat entry{};
    if (fstatat(parent, name, &entry, AT_SYMLINK_NOFOLLOW) != 0)
        return errno == ENOENT ? 0 : errno;
    if (!S_ISDIR(entry.st_mode)) {
        return unlinkat(parent, name, 0) == 0 || errno == ENOENT ? 0 : errno;
    }
    if (depth == 0) return ELOOP;
    DIR *directory = directory_at(parent, name, true);
    if (!directory) return errno == ENOENT ? 0 : errno;
    int status = 0;
    while (status == 0) {
        errno = 0;
        const auto *child = readdir(directory);
        if (!child) {
            status = errno;
            break;
        }
        if (!dot_entry(child->d_name))
            status = __vqr_io_remove_tree_at(dirfd(directory), child->d_name, depth - 1);
    }
    if (closedir(directory) != 0 && status == 0) status = errno;
    if (status == 0 && unlinkat(parent, name, AT_REMOVEDIR) != 0 && errno != ENOENT) status = errno;
    return status;
}

uintptr_t __vqr_io_dir_open(const char *path, int32_t *out_error) {
    DIR *directory = opendir(path);
    if (!directory) {
        set_error(out_error, errno);
        return 0;
    }
    set_error(out_error, 0);
    return (uintptr_t)directory;
}

int32_t __vqr_io_dir_next(void *opaque, uint8_t *name, size_t capacity, size_t *length,
                          int32_t *kind, int32_t *out_error) {
    DIR *directory = (DIR *)opaque;
    for (;;) {
        errno = 0;
        struct dirent *entry = readdir(directory);
        if (!entry) {
            if (errno) {
                set_error(out_error, errno);
                return -1;
            }
            set_error(out_error, 0);
            return 0;
        }
        if ((entry->d_name[0] == '.' && entry->d_name[1] == '\0') ||
            (entry->d_name[0] == '.' && entry->d_name[1] == '.' && entry->d_name[2] == '\0'))
            continue;
        size_t count = strnlen(entry->d_name, capacity);
        if (count >= capacity) {
            set_error(out_error, ENAMETOOLONG);
            return -1;
        }
        memcpy(name, entry->d_name, count);
        if (length) *length = count;
        int result_kind = 5;
        switch (entry->d_type) {
        case DT_REG: result_kind = 0; break;
        case DT_DIR: result_kind = 1; break;
        case DT_LNK: result_kind = 2; break;
        case DT_CHR:
        case DT_BLK:
        case DT_FIFO: result_kind = 3; break;
        case DT_SOCK: result_kind = 4; break;
        case DT_UNKNOWN: {
            struct stat value;
            if (fstatat(dirfd(directory), entry->d_name, &value, AT_SYMLINK_NOFOLLOW) != 0) {
                if (errno == ENOENT) continue;
                set_error(out_error, errno);
                return -1;
            }
            result_kind = file_kind(value.st_mode);
            break;
        }
        default: break;
        }
        if (kind) *kind = result_kind;
        set_error(out_error, 0);
        return 1;
    }
}

int32_t __vqr_io_dir_close(void *opaque, int32_t *out_error) {
    if (closedir((DIR *)opaque) != 0) {
        set_error(out_error, errno);
        return -1;
    }
    set_error(out_error, 0);
    return 0;
}

int32_t __vqr_io_getcwd(uint8_t *buffer, size_t capacity, size_t *length, int32_t *out_error) {
    if (!getcwd((char *)buffer, capacity)) {
        set_error(out_error, errno);
        return -1;
    }
    if (length) *length = strlen((const char *)buffer);
    set_error(out_error, 0);
    return 0;
}

int32_t __vqr_io_chdir(const char *path, int32_t *out_error) {
    if (chdir(path) != 0) {
        set_error(out_error, errno);
        return -1;
    }
    set_error(out_error, 0);
    return 0;
}

int32_t __vqr_io_format_float(double value, int32_t style, int32_t precision,
                              uint8_t *buffer, size_t capacity, size_t *length,
                              int32_t *out_error) {
    if (capacity == 0 || precision < -1 || precision > 64 || style < 0 || style > 2) {
        set_error(out_error, EINVAL);
        return -1;
    }
    locale_t locale = newlocale(LC_NUMERIC_MASK, "C", nullptr);
    if (!locale) {
        set_error(out_error, errno);
        return -1;
    }
    locale_t previous = uselocale(locale);
    if (!previous) {
        int reason = errno;
        freelocale(locale);
        set_error(out_error, reason);
        return -1;
    }
    const char *const formats[] = {"%.*g", "%.*f", "%.*e"};
    int count = snprintf((char *)buffer, capacity, formats[style], precision < 0 ? 6 : precision, value);
    uselocale(previous);
    freelocale(locale);
    if (count < 0) {
        set_error(out_error, EILSEQ);
        return -1;
    }
    if ((size_t)count >= capacity) {
        set_error(out_error, ERANGE);
        return -1;
    }
    if (length) *length = (size_t)count;
    set_error(out_error, 0);
    return 0;
}

int __vqr_io_env_lock() { return pthread_mutex_lock(&environment_mutex); }
void __vqr_io_env_unlock() { pthread_mutex_unlock(&environment_mutex); }

uintptr_t __vqr_io_env_get(const char *key, size_t *length, int32_t *out_error) {
    EnvironmentLock lock;
    set_error(out_error, lock.status);
    if (lock.status != 0) return 0;
    const char *value = getenv(key);
    if (!value) return 0;
    char *copy = strdup(value);
    if (!copy) {
        set_error(out_error, errno);
        return 0;
    }
    *length = strlen(copy);
    return reinterpret_cast<uintptr_t>(copy);
}

void __vqr_io_env_free_string(uintptr_t address) {
    free(reinterpret_cast<void *>(address));
}

int32_t __vqr_io_env_set(const char *key, const char *value) {
    EnvironmentLock lock;
    if (lock.status != 0) return lock.status;
    return setenv(key, value, 1) == 0 ? 0 : errno;
}

int32_t __vqr_io_env_remove(const char *key) {
    EnvironmentLock lock;
    if (lock.status != 0) return lock.status;
    return unsetenv(key) == 0 ? 0 : errno;
}

uintptr_t __vqr_io_env_snapshot(size_t *length, int32_t *out_error) {
    EnvironmentLock lock;
    set_error(out_error, lock.status);
    if (lock.status != 0) return 0;
    size_t count = 0;
    while (environ && environ[count]) ++count;
    auto **copy = static_cast<char **>(calloc(count == 0 ? 1 : count, sizeof(char *)));
    if (!copy) {
        set_error(out_error, errno);
        return 0;
    }
    for (size_t index = 0; index < count; ++index) {
        copy[index] = strdup(environ[index]);
        if (!copy[index]) {
            set_error(out_error, errno);
            free_environment(copy, index);
            return 0;
        }
    }
    *length = count;
    return reinterpret_cast<uintptr_t>(copy);
}

char *__vqr_io_env_entry(uintptr_t entries, size_t index, size_t *length) {
    char *entry = reinterpret_cast<char **>(entries)[index];
    *length = strlen(entry);
    return entry;
}

void __vqr_io_env_free_snapshot(uintptr_t entries, size_t count) {
    free_environment(reinterpret_cast<char **>(entries), count);
}

intptr_t __vqr_io_env_current_exe(uint8_t *buffer, size_t capacity, int32_t *out_error) {
    auto size = retry_interrupted([&] {
        return readlink("/proc/self/exe", reinterpret_cast<char *>(buffer), capacity);
    });
    set_error(out_error, size < 0 ? errno : 0);
    return size;
}

} // extern "C"
