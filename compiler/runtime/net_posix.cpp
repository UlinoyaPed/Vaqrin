#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif

#include <arpa/inet.h>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <fcntl.h>
#include <netdb.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

namespace {

// The first eight bytes hold the IP version, one reserved byte, the port and
// scope ID in network byte order. The remaining bytes hold the address.
constexpr size_t wire_size = 24;
constexpr int ipv4 = 4;
constexpr int ipv6 = 6;

template <class Operation>
auto retry_interrupted(Operation operation) {
    auto result = operation();
    while (result < 0 && errno == EINTR) result = operation();
    return result;
}

socklen_t decode_address(const uint8_t *wire, sockaddr_storage &storage) {
    if (wire[0] == ipv4) {
        auto *address = reinterpret_cast<sockaddr_in *>(&storage);
        address->sin_family = AF_INET;
        memcpy(&address->sin_port, wire + 2, 2);
        memcpy(&address->sin_addr, wire + 8, 4);
        return sizeof(*address);
    }
    if (wire[0] == ipv6) {
        auto *address = reinterpret_cast<sockaddr_in6 *>(&storage);
        address->sin6_family = AF_INET6;
        memcpy(&address->sin6_port, wire + 2, 2);
        memcpy(&address->sin6_scope_id, wire + 4, 4);
        address->sin6_scope_id = ntohl(address->sin6_scope_id);
        memcpy(&address->sin6_addr, wire + 8, 16);
        return sizeof(*address);
    }
    return 0;
}

bool encode_address(const sockaddr *source, socklen_t length, uint8_t *wire) {
    memset(wire, 0, wire_size);
    if (source->sa_family == AF_INET && length >= sizeof(sockaddr_in)) {
        const auto *address = reinterpret_cast<const sockaddr_in *>(source);
        wire[0] = ipv4;
        memcpy(wire + 2, &address->sin_port, 2);
        memcpy(wire + 8, &address->sin_addr, 4);
        return true;
    }
    if (source->sa_family == AF_INET6 && length >= sizeof(sockaddr_in6)) {
        const auto *address = reinterpret_cast<const sockaddr_in6 *>(source);
        const uint32_t scope = htonl(address->sin6_scope_id);
        wire[0] = ipv6;
        memcpy(wire + 2, &address->sin6_port, 2);
        memcpy(wire + 4, &scope, 4);
        memcpy(wire + 8, &address->sin6_addr, 16);
        return true;
    }
    return false;
}

int connect_socket(int fd, const sockaddr *address, socklen_t length, int64_t timeout = -1) {
    constexpr int64_t nanos_per_second = 1000000000;
    timespec deadline{};
    if (timeout >= 0) {
        if (clock_gettime(CLOCK_MONOTONIC, &deadline) != 0) return errno;
        deadline.tv_sec += timeout / nanos_per_second;
        deadline.tv_nsec += timeout % nanos_per_second;
        deadline.tv_sec += deadline.tv_nsec / nanos_per_second;
        deadline.tv_nsec %= nanos_per_second;
    }
    if (connect(fd, address, length) == 0) return 0;
    if (errno != EINTR && (timeout < 0 || errno != EINPROGRESS)) return errno;
    pollfd ready{fd, POLLOUT, 0};
    for (;;) {
        timespec remaining{};
        if (timeout >= 0) {
            timespec now{};
            if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return errno;
            remaining.tv_sec = deadline.tv_sec - now.tv_sec;
            remaining.tv_nsec = deadline.tv_nsec - now.tv_nsec;
            if (remaining.tv_nsec < 0) {
                --remaining.tv_sec;
                remaining.tv_nsec += nanos_per_second;
            }
            if (remaining.tv_sec < 0) return ETIMEDOUT;
        }
        const int count = ppoll(&ready, 1, timeout >= 0 ? &remaining : nullptr, nullptr);
        if (count > 0) break;
        if (count == 0) return ETIMEDOUT;
        if (errno != EINTR) return errno;
    }
    int error = 0;
    socklen_t size = sizeof(error);
    return getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &size) == 0 ? error : errno;
}

int set_nonblocking(int fd, bool enabled) {
    int flags = retry_interrupted([&] { return fcntl(fd, F_GETFL); });
    if (flags < 0) return errno;
    int changed = enabled ? flags | O_NONBLOCK : flags & ~O_NONBLOCK;
    if (changed == flags) return 0;
    return retry_interrupted([&] { return fcntl(fd, F_SETFL, changed); }) == 0 ? 0 : errno;
}

} // namespace

extern "C" {

int32_t __vqr_net_parse_ip(int32_t version, const char *text, uint8_t *out) {
    return inet_pton(version == ipv4 ? AF_INET : AF_INET6, text, out);
}

int32_t __vqr_net_format_ip(int32_t version, const uint8_t *data, char *out, size_t capacity) {
    if (!inet_ntop(version == ipv4 ? AF_INET : AF_INET6, data, out, capacity)) return -1;
    return static_cast<int32_t>(strlen(out));
}

uint16_t __vqr_net_htons(uint16_t value) { return htons(value); }
uint16_t __vqr_net_ntohs(uint16_t value) { return ntohs(value); }
uint32_t __vqr_net_htonl(uint32_t value) { return htonl(value); }
uint32_t __vqr_net_ntohl(uint32_t value) { return ntohl(value); }

int32_t __vqr_net_open(const uint8_t *wire, int32_t operation, int64_t timeout, int32_t *out_error) {
    sockaddr_storage storage{};
    const socklen_t length = decode_address(wire, storage);
    const auto *address = reinterpret_cast<const sockaddr *>(&storage);
    if (length == 0) {
        *out_error = EAFNOSUPPORT;
        return -1;
    }
    const int type = operation == 2 ? SOCK_DGRAM : SOCK_STREAM;
    const bool timed = operation == 0 && timeout >= 0;
    const bool asynchronous = operation == 3;
    const int fd = socket(address->sa_family, type | SOCK_CLOEXEC |
                          ((timed || asynchronous) ? SOCK_NONBLOCK : 0), 0);
    if (fd < 0) {
        *out_error = errno;
        return -1;
    }
    int error = 0;
    if (asynchronous) {
        if (connect(fd, address, length) != 0 && errno != EINPROGRESS && errno != EINTR)
            error = errno;
    } else if (operation == 0) {
        error = connect_socket(fd, address, length, timeout);
        if (error == 0 && timed) error = set_nonblocking(fd, false);
    } else {
        if (operation == 1) {
            const int reuse = 1;
            if (setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse)) != 0) error = errno;
        }
        if (error == 0 && bind(fd, address, length) != 0) error = errno;
        if (error == 0 && operation == 1 && listen(fd, SOMAXCONN) != 0) error = errno;
    }
    *out_error = error;
    if (error == 0) return fd;
    close(fd);
    return -1;
}

int32_t __vqr_net_socket_error(int32_t fd) {
    int error = 0;
    socklen_t length = sizeof(error);
    return getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &length) == 0 ? error : errno;
}

int32_t __vqr_net_connect(int32_t fd, const uint8_t *wire) {
    sockaddr_storage storage{};
    const socklen_t length = decode_address(wire, storage);
    if (length == 0) return EAFNOSUPPORT;
    return connect_socket(fd, reinterpret_cast<const sockaddr *>(&storage), length);
}

int32_t __vqr_net_accept(int32_t fd, uint8_t *wire, int32_t *out_error) {
    sockaddr_storage storage{};
    socklen_t length = sizeof(storage);
    auto *address = reinterpret_cast<sockaddr *>(&storage);
    const int client = retry_interrupted([&] { return accept4(fd, address, &length, SOCK_CLOEXEC); });
    if (client < 0) {
        *out_error = errno;
        return -1;
    }
    if (encode_address(address, length, wire)) {
        *out_error = 0;
        return client;
    }
    close(client);
    *out_error = EAFNOSUPPORT;
    return -1;
}

int32_t __vqr_net_address(int32_t fd, int32_t peer, uint8_t *wire) {
    sockaddr_storage storage{};
    socklen_t length = sizeof(storage);
    auto *address = reinterpret_cast<sockaddr *>(&storage);
    if ((peer ? getpeername(fd, address, &length) : getsockname(fd, address, &length)) != 0) return errno;
    return encode_address(address, length, wire) ? 0 : EAFNOSUPPORT;
}

int32_t __vqr_net_close(int32_t fd) { return close(fd) == 0 ? 0 : errno; }

int32_t __vqr_net_shutdown(int32_t fd, int32_t direction) {
    return shutdown(fd, direction == 0 ? SHUT_RD : SHUT_WR) == 0 ? 0 : errno;
}

int32_t __vqr_net_clone(int32_t fd, int32_t *out_error) {
    const int duplicate = retry_interrupted([&] { return fcntl(fd, F_DUPFD_CLOEXEC, 0); });
    *out_error = duplicate < 0 ? errno : 0;
    return duplicate;
}

int32_t __vqr_net_set_option(int32_t fd, int32_t option, int32_t value) {
    if (option == 0) return set_nonblocking(fd, value != 0);
    const int level = option == 1 ? IPPROTO_TCP : SOL_SOCKET;
    const int name = option == 1 ? TCP_NODELAY : SO_BROADCAST;
    return setsockopt(fd, level, name, &value, sizeof(value)) == 0 ? 0 : errno;
}

int32_t __vqr_net_set_timeout(int32_t fd, int32_t writing, int64_t seconds, int32_t nanos) {
    const auto micros = (nanos + 999) / 1000;
    if (seconds == INT64_MAX && micros == 1000000) return EOVERFLOW;
    const struct timeval timeout{seconds + micros / 1000000, micros % 1000000};
    const int option = writing ? SO_SNDTIMEO : SO_RCVTIMEO;
    return setsockopt(fd, SOL_SOCKET, option, &timeout, sizeof(timeout)) == 0 ? 0 : errno;
}

intptr_t __vqr_net_receive(int32_t fd, uint8_t *data, size_t size, int32_t *out_error) {
    auto count = retry_interrupted([&] { return recv(fd, data, size, 0); });
    *out_error = count < 0 ? errno : 0;
    return count;
}

intptr_t __vqr_net_send(int32_t fd, const uint8_t *data, size_t size, int32_t *out_error) {
    auto count = retry_interrupted([&] { return send(fd, data, size, MSG_NOSIGNAL); });
    *out_error = count < 0 ? errno : 0;
    return count;
}

intptr_t __vqr_net_send_to(int32_t fd, const uint8_t *data, size_t size, const uint8_t *wire,
                           int32_t *out_error) {
    sockaddr_storage storage{};
    const socklen_t length = decode_address(wire, storage);
    if (length == 0) {
        *out_error = EAFNOSUPPORT;
        return -1;
    }
    const auto *address = reinterpret_cast<const sockaddr *>(&storage);
    auto count = retry_interrupted([&] { return sendto(fd, data, size, MSG_NOSIGNAL, address, length); });
    *out_error = count < 0 ? errno : 0;
    return count;
}

intptr_t __vqr_net_receive_from(int32_t fd, uint8_t *data, size_t size, uint8_t *wire,
                                int32_t *out_error) {
    sockaddr_storage storage{};
    socklen_t length = sizeof(storage);
    auto *address = reinterpret_cast<sockaddr *>(&storage);
    auto count = retry_interrupted([&] { return recvfrom(fd, data, size, MSG_TRUNC, address, &length); });
    if (count < 0) {
        *out_error = errno;
        return -1;
    }
    if (!encode_address(address, length, wire)) {
        *out_error = EAFNOSUPPORT;
        return -1;
    }
    *out_error = 0;
    return count;
}

uintptr_t __vqr_net_resolve(const char *name, int32_t *out_error) {
    addrinfo hints{};
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    addrinfo *addresses = nullptr;
    const int error = getaddrinfo(name, nullptr, &hints, &addresses);
    *out_error = error == 0 ? 0 : error == EAI_SYSTEM ? errno : -1;
    return reinterpret_cast<uintptr_t>(addresses);
}

int32_t __vqr_net_next_address(uintptr_t *cursor, uint8_t *wire) {
    auto *entry = reinterpret_cast<const addrinfo *>(*cursor);
    while (entry) {
        if (encode_address(entry->ai_addr, entry->ai_addrlen, wire)) {
            *cursor = reinterpret_cast<uintptr_t>(entry->ai_next);
            return 1;
        }
        entry = entry->ai_next;
    }
    *cursor = 0;
    return 0;
}

void __vqr_net_free_addresses(uintptr_t address) {
    if (address) freeaddrinfo(reinterpret_cast<addrinfo *>(address));
}

} // extern "C"
