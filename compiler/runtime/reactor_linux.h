#ifndef VAQRIN_REACTOR_LINUX_H
#define VAQRIN_REACTOR_LINUX_H

#include <fcntl.h>
#include <stdint.h>
#include <sys/epoll.h>
#include <unistd.h>

enum vqr_interest { VQR_READ = 1, VQR_WRITE = 2, VQR_HANGUP = 4, VQR_ERROR = 8 };

static inline int vqr_epoll_set(int epoll, int op, int fd, uint64_t token,
                                 uint32_t interests, int one_shot) {
    struct epoll_event event = {.events = 0, .data = {.u64 = 0}};
    event.events = EPOLLRDHUP;
    if (interests & VQR_READ) event.events |= EPOLLIN;
    if (interests & VQR_WRITE) event.events |= EPOLLOUT;
    if (one_shot) event.events |= EPOLLONESHOT;
    event.data.u64 = token;
    return epoll_ctl(epoll, op, fd, &event);
}

static inline uint32_t vqr_epoll_ready(uint32_t events) {
    uint32_t result = 0;
    if (events & EPOLLIN) result |= VQR_READ;
    if (events & EPOLLOUT) result |= VQR_WRITE;
    if (events & (EPOLLHUP | EPOLLRDHUP)) result |= VQR_HANGUP;
    if (events & EPOLLERR) result |= VQR_ERROR;
    return result;
}

#endif
