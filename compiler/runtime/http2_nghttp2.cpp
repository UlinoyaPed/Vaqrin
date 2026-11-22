#if !__has_include(<nghttp2/nghttp2.h>)
#error "Vaqrin HTTP/2 requires nghttp2 development headers (libnghttp2-dev)"
#endif

#include <nghttp2/nghttp2.h>
#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <cstring>

namespace {
enum Status : int32_t { protocol = -1, exhausted = -2, invalid = -3, stream_closed = -4 };
enum EventKind : uint32_t { headers = 1, data, end_stream, closed, goaway, settings, ping, invalid_frame };

struct Event {
    Event *next;
    uint32_t kind, stream, code;
    size_t length;
    uint8_t *payload;
};

struct Packet {
    Packet *next;
    size_t length, offset;
};
struct Outgoing {
    Outgoing *next;
    int32_t stream;
    Packet *head, *tail;
    size_t trailer_length;
    uint8_t *trailers;
    bool finished, deferred;
};

struct Connection {
    nghttp2_session *session;
    Event *head, *tail;
    Outgoing *outgoing;
    int32_t failure;
    size_t queue_bytes, queue_count, output_bytes, output_count;
    size_t header_limit, output_limit, current_header_cost, current_header_size, current_header_capacity;
    uint8_t *current_headers;
    unsigned int continuations;
};

Connection &get(uintptr_t handle) { return *reinterpret_cast<Connection *>(handle); }
uintptr_t address(const void *pointer) { return reinterpret_cast<uintptr_t>(pointer); }

void write_u32(uint8_t *target, uint32_t value) {
    for (int i = 0; i < 4; ++i) target[i] = static_cast<uint8_t>(value >> (24 - 8 * i));
}
uint32_t read_u32(const uint8_t *source) {
    uint32_t value = 0;
    for (int i = 0; i < 4; ++i) value = (value << 8) | source[i];
    return value;
}

int32_t convert(Connection &connection, int result) {
    if (connection.failure != 0) return connection.failure;
    switch (result) {
    case NGHTTP2_ERR_NOMEM: return exhausted;
    case NGHTTP2_ERR_INVALID_ARGUMENT: return invalid;
    case NGHTTP2_ERR_STREAM_CLOSED: case NGHTTP2_ERR_STREAM_CLOSING: return stream_closed;
    default: return result >= 0 ? result : protocol;
    }
}

int abort_receive(Connection &connection, int32_t error) {
    connection.failure = error;
    return NGHTTP2_ERR_CALLBACK_FAILURE;
}

void discard(Event *event) {
    std::free(event->payload);
    std::free(event);
}

int event(Connection &connection, EventKind kind, int32_t stream, uint32_t code,
          const uint8_t *payload = nullptr, size_t length = 0) {
    constexpr size_t max_events = 2048;
    if (connection.queue_count == max_events || length > connection.output_limit - connection.queue_bytes)
        return abort_receive(connection, exhausted);
    auto *message = static_cast<Event *>(std::calloc(1, sizeof(Event)));
    if (!message) return abort_receive(connection, exhausted);
    if (length != 0) {
        message->payload = static_cast<uint8_t *>(std::malloc(length));
        if (!message->payload) {
            std::free(message);
            return abort_receive(connection, exhausted);
        }
        std::memcpy(message->payload, payload, length);
    }
    message->kind = kind;
    message->stream = static_cast<uint32_t>(stream);
    message->code = code;
    message->length = length;
    if (connection.tail) connection.tail->next = message;
    else connection.head = message;
    connection.tail = message;
    connection.queue_bytes += length;
    ++connection.queue_count;
    return 0;
}

int begin_frame(nghttp2_session *, const nghttp2_frame_hd *frame, void *user) {
    auto &connection = *static_cast<Connection *>(user);
    if (frame->type == NGHTTP2_HEADERS || frame->type == NGHTTP2_PUSH_PROMISE)
        connection.continuations = 0;
    if (frame->type == NGHTTP2_CONTINUATION && ++connection.continuations > 8)
        return abort_receive(connection, exhausted);
    return 0;
}

int start_headers(nghttp2_session *, const nghttp2_frame *, void *user) {
    auto &connection = *static_cast<Connection *>(user);
    connection.current_header_cost = 0;
    connection.current_header_size = 0;
    return 0;
}

int header(nghttp2_session *, const nghttp2_frame *, const uint8_t *name, size_t name_length,
           const uint8_t *value, size_t value_length, uint8_t flags, void *user) {
    auto &connection = *static_cast<Connection *>(user);
    const auto available = connection.header_limit - connection.current_header_cost;
    if (available < 32 || name_length > available - 32 ||
        value_length > available - 32 - name_length)
        return NGHTTP2_ERR_TEMPORAL_CALLBACK_FAILURE;
    connection.current_header_cost += 32 + name_length + value_length;
    const auto needed = connection.current_header_size + 8 + name_length + value_length;
    if (needed > connection.current_header_capacity) {
        const auto capacity = std::min(connection.header_limit,
            std::max(needed, connection.current_header_capacity * 2));
        auto *buffer = static_cast<uint8_t *>(std::realloc(connection.current_headers, capacity));
        if (!buffer) return abort_receive(connection, exhausted);
        connection.current_headers = buffer;
        connection.current_header_capacity = capacity;
    }
    auto *target = connection.current_headers + connection.current_header_size;
    const uint32_t marked = flags & NGHTTP2_NV_FLAG_NO_INDEX ? 0x80000000u : 0;
    write_u32(target, static_cast<uint32_t>(name_length) | marked);
    write_u32(target + 4, static_cast<uint32_t>(value_length));
    if (name_length) std::memcpy(target + 8, name, name_length);
    if (value_length) std::memcpy(target + 8 + name_length, value, value_length);
    connection.current_header_size = needed;
    return 0;
}

int received(nghttp2_session *, const nghttp2_frame *frame, void *user) {
    auto &connection = *static_cast<Connection *>(user);
    const auto id = frame->hd.stream_id;
    switch (frame->hd.type) {
    case NGHTTP2_HEADERS: {
        const int status = event(connection, headers, id, frame->hd.flags,
            connection.current_headers, connection.current_header_size);
        if (status != 0) return status;
        break;
    }
    case NGHTTP2_GOAWAY:
        return event(connection, goaway, frame->goaway.last_stream_id, frame->goaway.error_code,
                     frame->goaway.opaque_data, frame->goaway.opaque_data_len);
    case NGHTTP2_SETTINGS: return event(connection, settings, 0, frame->hd.flags);
    case NGHTTP2_PING: return event(connection, ping, 0, frame->hd.flags, frame->ping.opaque_data, 8);
    }
    if ((frame->hd.type == NGHTTP2_HEADERS || frame->hd.type == NGHTTP2_DATA) &&
        (frame->hd.flags & NGHTTP2_FLAG_END_STREAM))
        return event(connection, end_stream, id, 0);
    return 0;
}

int invalid_received(nghttp2_session *, const nghttp2_frame *frame, int error, void *user) {
    return event(*static_cast<Connection *>(user), invalid_frame, frame->hd.stream_id,
                 static_cast<uint32_t>(-error));
}

int receive_data(nghttp2_session *, uint8_t, int32_t stream, const uint8_t *bytes,
                 size_t length, void *user) {
    if (length == 0) return 0;
    return event(*static_cast<Connection *>(user), data, stream, 0, bytes, length);
}

Outgoing *find_outgoing(Connection &connection, int32_t id) {
    for (auto *output = connection.outgoing; output; output = output->next)
        if (output->stream == id) return output;
    return nullptr;
}
void discard_packet(Connection &connection, Packet *packet) {
    connection.output_bytes -= packet->length;
    --connection.output_count;
    std::free(packet);
}
void remove_outgoing(Connection &connection, int32_t stream) {
    auto **cursor = &connection.outgoing;
    while (*cursor && (*cursor)->stream != stream) cursor = &(*cursor)->next;
    if (!*cursor) return;
    auto *output = *cursor;
    *cursor = output->next;
    while (output->head) {
        auto *packet = output->head;
        output->head = packet->next;
        discard_packet(connection, packet);
    }
    connection.output_bytes -= output->trailer_length;
    std::free(output->trailers);
    std::free(output);
}
int close_stream(nghttp2_session *, int32_t stream, uint32_t code, void *user) {
    auto &connection = *static_cast<Connection *>(user);
    remove_outgoing(connection, stream);
    return event(connection, closed, stream, code);
}

int not_sent(nghttp2_session *, const nghttp2_frame *frame, int error, void *user) {
    if (frame->hd.type != NGHTTP2_HEADERS) return 0;
    auto &connection = *static_cast<Connection *>(user);
    const auto stream = frame->hd.stream_id;
    remove_outgoing(connection, stream);
    return event(connection, closed, stream,
                 error == NGHTTP2_ERR_START_STREAM_NOT_ALLOWED ? NGHTTP2_REFUSED_STREAM : NGHTTP2_INTERNAL_ERROR);
}

bool unpack(const uint8_t *wire, size_t length, size_t limit,
            nghttp2_nv *fields, size_t &count, bool trailer) {
    count = 0;
    size_t cost = 0;
    bool regular = false;
    while (length != 0) {
        if (length < 8 || count == 256 || limit - cost < 32) return false;
        const uint32_t marked = read_u32(wire);
        const size_t name_length = marked & 0x7fffffffu;
        const size_t value_length = read_u32(wire + 4);
        wire += 8;
        length -= 8;
        if (name_length == 0 || name_length > length || value_length > length - name_length ||
            name_length > limit - cost - 32 || value_length > limit - cost - 32 - name_length)
            return false;
        const auto *value = wire + name_length;
        if (!nghttp2_check_header_name(wire, name_length) || !nghttp2_check_header_value(value, value_length))
            return false;
        if (*wire == ':') {
            if (regular || trailer) return false;
        } else {
            regular = true;
        }
        const auto flags = marked >> 31 ? NGHTTP2_NV_FLAG_NO_INDEX : NGHTTP2_NV_FLAG_NONE;
        fields[count++] = {const_cast<uint8_t *>(wire), const_cast<uint8_t *>(value),
                           name_length, value_length, static_cast<uint8_t>(flags)};
        const auto field_size = name_length + value_length;
        cost += field_size + 32;
        wire += field_size;
        length -= field_size;
    }
    return true;
}

ssize_t provide(nghttp2_session *session, int32_t stream, uint8_t *buffer, size_t length,
                uint32_t *flags, nghttp2_data_source *source, void *user) {
    auto &connection = *static_cast<Connection *>(user);
    auto &output = *static_cast<Outgoing *>(source->ptr);
    size_t written = 0;
    while (output.head && written < length) {
        auto *packet = output.head;
        const auto count = std::min(length - written, packet->length - packet->offset);
        std::memcpy(buffer + written, reinterpret_cast<const uint8_t *>(packet + 1) + packet->offset, count);
        packet->offset += count;
        written += count;
        if (packet->offset == packet->length) {
            output.head = packet->next;
            if (!output.head) output.tail = nullptr;
            discard_packet(connection, packet);
        }
    }
    if (!output.head && output.finished) {
        *flags |= NGHTTP2_DATA_FLAG_EOF;
        if (output.trailers) {
            nghttp2_nv fields[256];
            size_t count = 0;
            if (!unpack(output.trailers, output.trailer_length, connection.header_limit, fields, count, true))
                return abort_receive(connection, invalid);
            const auto status = nghttp2_submit_trailer(session, stream, fields, count);
            if (status != 0) return abort_receive(connection, convert(connection, status));
            *flags |= NGHTTP2_DATA_FLAG_NO_END_STREAM;
            connection.output_bytes -= output.trailer_length;
            std::free(output.trailers);
            output.trailers = nullptr;
            output.trailer_length = 0;
        }
    } else if (written == 0) {
        output.deferred = true;
        return NGHTTP2_ERR_DEFERRED;
    }
    return static_cast<ssize_t>(written);
}

void release(Connection *connection) {
    if (!connection) return;
    if (connection->session) nghttp2_session_del(connection->session);
    while (connection->outgoing) remove_outgoing(*connection, connection->outgoing->stream);
    while (connection->head) {
        auto *current = connection->head;
        connection->head = current->next;
        discard(current);
    }
    std::free(connection->current_headers);
    std::free(connection);
}
} // namespace

extern "C" uintptr_t __vqr_http2_open(int32_t server, uint32_t streams, uint32_t window,
                                      uint32_t table, uint32_t header_bytes, uint32_t queue_bytes,
                                      int32_t *status) {
    if (streams == 0 || streams > 256 || window > 0x7fffffffu || table > 65536 ||
        header_bytes < 256 || header_bytes > 262144 || queue_bytes < header_bytes ||
        queue_bytes > 64 * 1024 * 1024) {
        *status = invalid;
        return 0;
    }
    auto *connection = static_cast<Connection *>(std::calloc(1, sizeof(Connection)));
    *status = exhausted;
    if (!connection) return 0;
    connection->header_limit = header_bytes;
    connection->output_limit = queue_bytes;
    nghttp2_session_callbacks *callbacks = nullptr;
    nghttp2_option *options = nullptr;
    int rc = nghttp2_session_callbacks_new(&callbacks);
    if (rc == 0) rc = nghttp2_option_new(&options);
    if (rc == 0) {
        nghttp2_session_callbacks_set_on_begin_frame_callback(callbacks, begin_frame);
        nghttp2_session_callbacks_set_on_begin_headers_callback(callbacks, start_headers);
        nghttp2_session_callbacks_set_on_header_callback(callbacks, header);
        nghttp2_session_callbacks_set_on_frame_recv_callback(callbacks, received);
        nghttp2_session_callbacks_set_on_invalid_frame_recv_callback(callbacks, invalid_received);
        nghttp2_session_callbacks_set_on_frame_not_send_callback(callbacks, not_sent);
        nghttp2_session_callbacks_set_on_data_chunk_recv_callback(callbacks, receive_data);
        nghttp2_session_callbacks_set_on_stream_close_callback(callbacks, close_stream);
        nghttp2_option_set_no_auto_window_update(options, 1);
        nghttp2_option_set_max_send_header_block_length(options, header_bytes);
        nghttp2_option_set_max_deflate_dynamic_table_size(options, table);
        nghttp2_option_set_max_outbound_ack(options, 32);
        nghttp2_option_set_max_settings(options, 32);
        rc = server ? nghttp2_session_server_new2(&connection->session, callbacks, connection, options)
                    : nghttp2_session_client_new2(&connection->session, callbacks, connection, options);
    }
    nghttp2_option_del(options);
    nghttp2_session_callbacks_del(callbacks);
    if (rc == 0) {
        nghttp2_settings_entry capabilities[] = {
            {NGHTTP2_SETTINGS_MAX_CONCURRENT_STREAMS, streams},
            {NGHTTP2_SETTINGS_INITIAL_WINDOW_SIZE, window},
            {NGHTTP2_SETTINGS_HEADER_TABLE_SIZE, table},
            {NGHTTP2_SETTINGS_MAX_HEADER_LIST_SIZE, header_bytes},
            {NGHTTP2_SETTINGS_ENABLE_PUSH, 0},
        };
        rc = nghttp2_submit_settings(connection->session, NGHTTP2_FLAG_NONE, capabilities, server ? 4 : 5);
    }
    *status = convert(*connection, rc);
    if (*status != 0) {
        release(connection);
        return 0;
    }
    return address(connection);
}

extern "C" void __vqr_http2_close(uintptr_t handle) { release(&get(handle)); }

extern "C" int32_t __vqr_http2_ingest(uintptr_t handle, const uint8_t *wire, size_t size) {
    auto &connection = get(handle);
    if (connection.failure != 0) return connection.failure;
    const auto count = nghttp2_session_mem_recv(connection.session, wire, size);
    if (count < 0) return convert(connection, static_cast<int>(count));
    return static_cast<size_t>(count) == size ? 0 : protocol;
}

extern "C" int32_t __vqr_http2_output(uintptr_t handle, uintptr_t *wire, size_t *length) {
    auto &connection = get(handle);
    if (connection.failure != 0) return connection.failure;
    const uint8_t *buffer = nullptr;
    const auto count = nghttp2_session_mem_send(connection.session, &buffer);
    if (count < 0) return convert(connection, static_cast<int>(count));
    *wire = address(buffer);
    *length = static_cast<size_t>(count);
    return 0;
}

extern "C" uintptr_t __vqr_http2_next(uintptr_t handle, uint32_t *info, size_t *length) {
    auto &connection = get(handle);
    auto *current = connection.head;
    if (!current) return 0;
    connection.head = current->next;
    if (!connection.head) connection.tail = nullptr;
    connection.queue_bytes -= current->length;
    --connection.queue_count;
    info[0] = current->kind;
    info[1] = current->stream;
    info[2] = current->code;
    *length = current->length;
    return address(current);
}

extern "C" void __vqr_http2_copy_event(uintptr_t handle, uint8_t *out) {
    const auto &message = *reinterpret_cast<const Event *>(handle);
    if (message.length) std::memcpy(out, message.payload, message.length);
}
extern "C" void __vqr_http2_drop_event(uintptr_t handle) { discard(reinterpret_cast<Event *>(handle)); }

extern "C" int32_t __vqr_http2_consume(uintptr_t handle, int32_t stream, size_t size) {
    auto &connection = get(handle);
    return convert(connection, nghttp2_session_consume(connection.session, stream, size));
}

extern "C" int32_t __vqr_http2_consume_connection(uintptr_t handle, size_t size) {
    auto &connection = get(handle);
    return convert(connection, nghttp2_session_consume_connection(connection.session, size));
}

extern "C" int32_t __vqr_http2_consume_stream(uintptr_t handle, int32_t stream, size_t size) {
    auto &connection = get(handle);
    return convert(connection, nghttp2_session_consume_stream(connection.session, stream, size));
}

extern "C" int32_t __vqr_http2_headers(uintptr_t handle, int32_t stream, int32_t end,
                                       const uint8_t *wire, size_t length) {
    auto &connection = get(handle);
    if (connection.failure) return connection.failure;
    nghttp2_nv fields[256];
    size_t count = 0;
    if (!unpack(wire, length, connection.header_limit, fields, count, false) || count == 0)
        return invalid;
    if (stream != -1 && find_outgoing(connection, stream)) return invalid;
    if (nghttp2_session_get_outbound_queue_size(connection.session) >= 256) return exhausted;
    Outgoing *output = nullptr;
    nghttp2_data_provider provider{};
    if (!end) {
        output = static_cast<Outgoing *>(std::calloc(1, sizeof(Outgoing)));
        if (!output) return exhausted;
        provider.source.ptr = output;
        provider.read_callback = provide;
    }
    const auto result = stream == -1
        ? nghttp2_submit_request(connection.session, nullptr, fields, count, output ? &provider : nullptr, nullptr)
        : nghttp2_submit_response(connection.session, stream, fields, count, output ? &provider : nullptr);
    if (result < 0) {
        std::free(output);
        return convert(connection, result);
    }
    if (output) {
        output->stream = stream == -1 ? result : stream;
        output->next = connection.outgoing;
        connection.outgoing = output;
    }
    return result;
}

extern "C" int32_t __vqr_http2_send(uintptr_t handle, int32_t stream, const uint8_t *data,
                                    size_t length, int32_t end) {
    auto &connection = get(handle);
    if (connection.failure) return connection.failure;
    auto *output = find_outgoing(connection, stream);
    if (!output || output->finished) return stream_closed;
    if (length == 0 && !end) return 0;
    if (length > connection.output_limit - connection.output_bytes || connection.output_count == 2048)
        return exhausted;
    Packet *packet = nullptr;
    if (length) {
        packet = static_cast<Packet *>(std::calloc(1, sizeof(Packet) + length));
        if (!packet) return exhausted;
        packet->length = length;
        std::memcpy(packet + 1, data, length);
    }
    if (output->deferred) {
        const int result = nghttp2_session_resume_data(connection.session, stream);
        if (result != 0) {
            std::free(packet);
            return convert(connection, result);
        }
        output->deferred = false;
    }
    if (packet) {
        connection.output_bytes += length;
        ++connection.output_count;
        if (output->tail) output->tail->next = packet;
        else output->head = packet;
        output->tail = packet;
    }
    output->finished = end != 0;
    return 0;
}

extern "C" int32_t __vqr_http2_has_pending_data(uintptr_t handle, int32_t stream) {
    auto &connection = get(handle);
    if (connection.failure) return connection.failure;
    auto *output = find_outgoing(connection, stream);
    if (!output) return stream_closed;
    return output->head != nullptr;
}

extern "C" int32_t __vqr_http2_trailers(uintptr_t handle, int32_t stream,
                                        const uint8_t *data, size_t length) {
    auto &connection = get(handle);
    if (connection.failure) return connection.failure;
    auto *output = find_outgoing(connection, stream);
    if (!output || output->finished) return stream_closed;
    if (length == 0) return invalid;
    if (length > connection.output_limit - connection.output_bytes) return exhausted;
    nghttp2_nv fields[256];
    size_t count = 0;
    if (!unpack(data, length, connection.header_limit, fields, count, true)) return invalid;
    auto *copy = static_cast<uint8_t *>(std::malloc(length));
    if (!copy) return exhausted;
    std::memcpy(copy, data, length);
    if (output->deferred) {
        const int result = nghttp2_session_resume_data(connection.session, stream);
        if (result != 0) {
            std::free(copy);
            return convert(connection, result);
        }
        output->deferred = false;
    }
    output->finished = true;
    output->trailers = copy;
    output->trailer_length = length;
    connection.output_bytes += length;
    return 0;
}

extern "C" int32_t __vqr_http2_reset(uintptr_t handle, int32_t stream, uint32_t code) {
    auto &connection = get(handle);
    return convert(connection, nghttp2_submit_rst_stream(connection.session, 0, stream, code));
}

extern "C" int32_t __vqr_http2_goaway(uintptr_t handle, uint32_t code) {
    auto &connection = get(handle);
    const auto last = nghttp2_session_get_last_proc_stream_id(connection.session);
    return convert(connection, nghttp2_submit_goaway(connection.session, 0, last, code, nullptr, 0));
}

extern "C" int32_t __vqr_http2_ping(uintptr_t handle, const uint8_t *token) {
    auto &connection = get(handle);
    return convert(connection, nghttp2_submit_ping(connection.session, 0, token));
}
