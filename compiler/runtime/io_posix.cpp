#define _GNU_SOURCE
#define _DEFAULT_SOURCE
#define _FILE_OFFSET_BITS 64

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

static void set_error(int32_t *out_error, int value) {
    if (out_error) *out_error = value;
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
                           int64_t *modified, int32_t *kind) {
    if (size) *size = value->st_size < 0 ? 0u : (uint64_t)value->st_size;
    if (mode) *mode = (uint32_t)(value->st_mode & 07777);
    if (modified) *modified = (int64_t)value->st_mtim.tv_sec;
    if (kind) *kind = (int32_t)file_kind(value->st_mode);
}

extern "C" {

int32_t __vqr_io_system_error(int32_t code) {
    return (int32_t)(0x80000000u | ((uint32_t)code & 0x7fffffffu));
}

int32_t __vqr_io_error_code(int32_t error_id) {
    uint32_t value = (uint32_t)error_id;
    if ((value & 0x80000000u) == 0) return -1;
    return (int32_t)(value & 0x7fffffffu);
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
    if (flags & 16u) native_flags |= O_EXCL;
    if (flags & 32u) native_flags |= O_TRUNC;
    int descriptor = open(path, native_flags, (mode_t)mode);
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
    ssize_t count = read(descriptor, dst, length);
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
    ssize_t count = write(descriptor, src, length);
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
                               int64_t *modified, int32_t *kind, int32_t *out_error) {
    struct stat value;
    if (fstat(descriptor, &value) != 0) {
        set_error(out_error, errno);
        return -1;
    }
    write_metadata(&value, size, mode, modified, kind);
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
                      int64_t *modified, int32_t *kind, int32_t *out_error) {
    struct stat value;
    int result = follow ? stat(path, &value) : lstat(path, &value);
    if (result != 0) {
        set_error(out_error, errno);
        return -1;
    }
    write_metadata(&value, size, mode, modified, kind);
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

void *__vqr_io_dir_open(const char *path, int32_t *out_error) {
    DIR *directory = opendir(path);
    if (!directory) {
        set_error(out_error, errno);
        return NULL;
    }
    set_error(out_error, 0);
    return directory;
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
            if (fstatat(dirfd(directory), entry->d_name, &value, AT_SYMLINK_NOFOLLOW) == 0)
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
    char format[16];
    char conversion = style == 1 ? 'f' : style == 2 ? 'e' : 'g';
    if (precision < 0)
        snprintf(format, sizeof(format), "%%g");
    else
        snprintf(format, sizeof(format), "%%.%d%c", precision, conversion);
    int count = snprintf((char *)buffer, capacity, format, value);
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

} // extern "C"
