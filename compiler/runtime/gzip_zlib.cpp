#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <cstring>

#include <zlib.h>

// Use an independent gzip member for each message. Allocation never exceeds
// the caller's wire or decoded size limit, even for highly compressed inputs.
extern "C" int32_t __vqr_gzip_transform(int32_t encode, const uint8_t *input, size_t length,
                                    size_t limit, size_t *address, size_t *size) {
    *address = 0;
    *size = 0;
    if (length > 0x7fffffffu || limit > 0x7fffffffu) return 1;
    z_stream stream{};
    int result = encode
        ? deflateInit2(&stream, Z_DEFAULT_COMPRESSION, Z_DEFLATED, MAX_WBITS + 16, 8, Z_DEFAULT_STRATEGY)
        : inflateInit2(&stream, MAX_WBITS + 16);
    if (result != Z_OK) return result == Z_MEM_ERROR ? 3 : 2;
    stream.next_in = const_cast<uint8_t *>(input);
    stream.avail_in = static_cast<uInt>(length);
    uint8_t *output = nullptr;
    size_t capacity = 0;
    size_t total = 0;
    int32_t status = 0;
    uint8_t buffer[8192];
    do {
        stream.next_out = buffer;
        stream.avail_out = sizeof(buffer);
        result = encode ? deflate(&stream, Z_FINISH) : inflate(&stream, Z_NO_FLUSH);
        if (result != Z_OK && result != Z_STREAM_END) {
            status = result == Z_MEM_ERROR ? 3 : 2;
            break;
        }
        size_t written = sizeof(buffer) - stream.avail_out;
        if (written > limit - total) {
            status = 1;
            break;
        }
        if (written) {
            if (total + written > capacity) {
                auto target = std::min(limit, std::max(total + written, capacity * 2));
                auto *next = static_cast<uint8_t *>(std::realloc(output, target));
                if (!next) { status = 3; break; }
                output = next;
                capacity = target;
            }
            std::memcpy(output + total, buffer, written);
            total += written;
        }
    } while (result != Z_STREAM_END);
    if (!status && stream.avail_in != 0) status = 2;
    if (encode) deflateEnd(&stream); else inflateEnd(&stream);
    if (status) {
        std::free(output);
        return status;
    }
    *address = reinterpret_cast<size_t>(output);
    *size = total;
    return 0;
}

extern "C" void __vqr_gzip_free(size_t address) {
    std::free(reinterpret_cast<void *>(address));
}
