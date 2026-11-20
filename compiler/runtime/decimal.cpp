#include <charconv>
#include <cstddef>
#include <cstdint>

namespace {

template <class Float>
int parseDecimal(const char *first, const char *last, Float *value, std::size_t *consumed) {
  auto *start = first;
  auto *significand = first;
  if (*significand == '+' || *significand == '-') ++significand;
  if (significand == last || (*significand != '.' &&
                             (*significand < '0' || *significand > '9')))
    return 1;
  if (*first == '+') ++first;

  auto result = std::from_chars(first, last, *value, std::chars_format::general);
  if (result.ec == std::errc::result_out_of_range) return 2;
  if (result.ec != std::errc{}) return 1;
  *consumed = static_cast<std::size_t>(result.ptr - start);
  return 0;
}

} // namespace

extern "C" std::int32_t __vqr_decimal_parse_f32(const char *data, std::size_t length,
                                                float *value, std::size_t *consumed) {
  return length == 0 ? 1 : parseDecimal(data, data + length, value, consumed);
}

extern "C" std::int32_t __vqr_decimal_parse_f64(const char *data, std::size_t length,
                                                double *value, std::size_t *consumed) {
  return length == 0 ? 1 : parseDecimal(data, data + length, value, consumed);
}

extern "C" std::size_t __vqr_decimal_format_f64(double value, char *buffer, std::size_t capacity) {
  auto result = std::to_chars(buffer, buffer + capacity, value, std::chars_format::scientific);
  return result.ec == std::errc{} ? static_cast<std::size_t>(result.ptr - buffer) : 0;
}
