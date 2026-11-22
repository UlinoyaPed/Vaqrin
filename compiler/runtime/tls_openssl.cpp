#include <cstddef>
#include <cstdint>

namespace {
enum TlsStatus : int32_t {
    unavailable = -1,
    setup_failed = -2,
    trust_store = -3,
    certificate = -4,
    handshake = -5,
    protocol = -6,
    missing_protocol = -7,
    server_identity = -8,
    unexpected_eof = -9,
    want_read = -10,
    want_write = -11,
};
}

// A plain HTTP program can be built and run without OpenSSL.
#if __has_include(<openssl/ssl.h>) && __has_include(<openssl/x509v3.h>)
#include <openssl/err.h>
#include <openssl/ssl.h>
#include <openssl/sslerr.h>
#include <openssl/x509v3.h>
#endif

#if defined(OPENSSL_VERSION_MAJOR) && OPENSSL_VERSION_MAJOR == 3
#include <arpa/inet.h>
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <pthread.h>
#include <sys/socket.h>

namespace {

#define VAQRIN_OPENSSL_FUNCTIONS(X) \
    X(TLS_client_method) \
    X(BIO_get_new_index) \
    X(BIO_meth_new) \
    X(BIO_meth_free) \
    X(BIO_meth_set_read) \
    X(BIO_meth_set_write) \
    X(BIO_meth_set_ctrl) \
    X(BIO_new) \
    X(BIO_get_data) \
    X(BIO_set_data) \
    X(BIO_set_init) \
    X(BIO_set_flags) \
    X(BIO_clear_flags) \
    X(TLS_server_method) \
    X(SSL_CTX_new) \
    X(SSL_CTX_free) \
    X(SSL_CTX_ctrl) \
    X(SSL_CTX_clear_options) \
    X(SSL_CTX_set_options) \
    X(SSL_CTX_set_verify) \
    X(SSL_CTX_set_default_verify_paths) \
    X(SSL_CTX_load_verify_locations) \
    X(SSL_CTX_use_certificate_chain_file) \
    X(SSL_CTX_use_PrivateKey_file) \
    X(SSL_CTX_check_private_key) \
    X(SSL_CTX_set_default_passwd_cb) \
    X(SSL_CTX_set_alpn_select_cb) \
    X(SSL_set_alpn_protos) \
    X(SSL_get0_alpn_selected) \
    X(SSL_select_next_proto) \
    X(SSL_new) \
    X(SSL_free) \
    X(SSL_set_bio) \
    X(SSL_ctrl) \
    X(SSL_set_hostflags) \
    X(SSL_set1_host) \
    X(SSL_get0_param) \
    X(X509_VERIFY_PARAM_set1_ip_asc) \
    X(SSL_connect) \
    X(SSL_accept) \
    X(SSL_get_verify_result) \
    X(SSL_read_ex) \
    X(SSL_write_ex) \
    X(SSL_get_error) \
    X(SSL_shutdown) \
    X(ERR_peek_last_error) \
    X(ERR_clear_error)

struct TlsApi {
#define DECLARE(name) decltype(&::name) name;
    VAQRIN_OPENSSL_FUNCTIONS(DECLARE)
#undef DECLARE
    bool ready;
};

TlsApi api{};
pthread_once_t load_once = PTHREAD_ONCE_INIT;

BIO_METHOD *socket_method = nullptr;

struct SocketState {
    int32_t fd;
    bool eof;
};

// The TCP stream owns the descriptor. TLS writes never raise SIGPIPE.
int socket_read(BIO *bio, char *data, int length) {
    api.BIO_clear_retry_flags(bio);
    auto &socket = *static_cast<SocketState *>(api.BIO_get_data(bio));
    const auto count = recv(socket.fd, data, length, 0);
    if (count == 0 && length != 0) socket.eof = true;
    if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) {
        const auto reason = errno;
        api.BIO_set_retry_read(bio);
        errno = reason;
    }
    return static_cast<int>(count);
}

int socket_write(BIO *bio, const char *data, int length) {
    api.BIO_clear_retry_flags(bio);
    const auto &socket = *static_cast<const SocketState *>(api.BIO_get_data(bio));
    const auto count = send(socket.fd, data, length, MSG_NOSIGNAL);
    if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) {
        const auto reason = errno;
        api.BIO_set_retry_write(bio);
        errno = reason;
    }
    return static_cast<int>(count);
}

long socket_control(BIO *bio, int operation, long, void *) {
    if (operation == BIO_CTRL_FLUSH) return 1;
    if (operation == BIO_CTRL_EOF) {
        const auto *socket = static_cast<const SocketState *>(api.BIO_get_data(bio));
        return socket != nullptr && socket->eof;
    }
    return 0;
}

bool load_socket_method() {
    const auto index = api.BIO_get_new_index();
    if (index < 0) return false;
    socket_method = api.BIO_meth_new(BIO_TYPE_SOURCE_SINK | index, "Vaqrin TCP socket");
    if (socket_method == nullptr) return false;
    if (api.BIO_meth_set_read(socket_method, socket_read) == 1 &&
        api.BIO_meth_set_write(socket_method, socket_write) == 1 &&
        api.BIO_meth_set_ctrl(socket_method, socket_control) == 1)
        return true;
    api.BIO_meth_free(socket_method);
    socket_method = nullptr;
    return false;
}

bool attach_socket(SSL *ssl, SocketState &socket) {
    BIO *bio = api.BIO_new(socket_method);
    if (bio == nullptr) return false;
    api.BIO_set_data(bio, &socket);
    api.BIO_set_init(bio, 1);
    api.SSL_set_bio(ssl, bio, bio);
    return true;
}

void load_openssl() {
    void *library = dlopen("libssl.so.3", RTLD_NOW | RTLD_LOCAL);
    if (library == nullptr) return;
    bool complete = true;
#define LOAD(name) \
    api.name = reinterpret_cast<decltype(api.name)>(dlsym(library, #name)); \
    complete = complete && api.name != nullptr;
    VAQRIN_OPENSSL_FUNCTIONS(LOAD)
#undef LOAD
    api.ready = complete && load_socket_method();
    if (!api.ready) dlclose(library);
}

#undef VAQRIN_OPENSSL_FUNCTIONS

struct Alpn {
    const unsigned char *protocols;
    unsigned int length;
    bool required;
};

struct Session {
    SSL *ssl;
    SocketState socket;
    Alpn alpn;
    bool server;
    bool nonblocking;
    bool handshaken;
    bool failed;
};

struct TlsResult {
    int error;
    int32_t status;
};

template <class Operation>
TlsResult run_tls(SSL *ssl, Operation operation) {
    for (;;) {
        api.ERR_clear_error();
        errno = 0;
        const int result = operation();
        const int reason = errno;
        if (result > 0) return {SSL_ERROR_NONE, 0};
        const int error = api.SSL_get_error(ssl, result);
        if (error == SSL_ERROR_WANT_READ || error == SSL_ERROR_WANT_WRITE) {
            if (reason == EINTR) continue;
            return {error, error == SSL_ERROR_WANT_READ ? want_read : want_write};
        }
        if (error == SSL_ERROR_SSL) {
            const auto detail = api.ERR_peek_last_error();
            if (ERR_SYSTEM_ERROR(detail)) return {error, ERR_GET_REASON(detail)};
            if (ERR_GET_LIB(detail) == ERR_LIB_SSL &&
                ERR_GET_REASON(detail) == SSL_R_UNEXPECTED_EOF_WHILE_READING)
                return {error, unexpected_eof};
        }
        return {error, error == SSL_ERROR_SYSCALL ? reason : 0};
    }
}

bool configure_identity(SSL *ssl, const char *host) {
    unsigned char address[16];
    if (inet_pton(AF_INET, host, address) == 1 || inet_pton(AF_INET6, host, address) == 1)
        return api.X509_VERIFY_PARAM_set1_ip_asc(api.SSL_get0_param(ssl), host) == 1;
    api.SSL_set_hostflags(ssl, X509_CHECK_FLAG_NO_PARTIAL_WILDCARDS | X509_CHECK_FLAG_NEVER_CHECK_SUBJECT);
    return api.SSL_set1_host(ssl, host) == 1 && api.SSL_set_tlsext_host_name(ssl, host) == 1;
}

int select_alpn(SSL *, const unsigned char **selected, unsigned char *size,
                const unsigned char *peer, unsigned int peer_size, void *data) {
    const auto &alpn = *static_cast<const Alpn *>(data);
    unsigned char *result = nullptr;
    if (api.SSL_select_next_proto(&result, size, alpn.protocols, alpn.length, peer, peer_size)
        != OPENSSL_NPN_NEGOTIATED)
        return alpn.required ? SSL_TLSEXT_ERR_ALERT_FATAL : SSL_TLSEXT_ERR_NOACK;
    *selected = result;
    return SSL_TLSEXT_ERR_OK;
}

int no_password(char *, int, int, void *) { return 0; }

SSL *prepare_tls(Session &session, const char *host, const char *ca, const char *cert,
                 const char *key, int32_t version, int32_t &status) {
    const bool server = session.server;
    auto &alpn = session.alpn;
    status = setup_failed;
    SSL_CTX *context = api.SSL_CTX_new(server ? api.TLS_server_method() : api.TLS_client_method());
    if (context == nullptr) return nullptr;
    SSL *ssl = nullptr;
    const int minimum = version == 13 ? TLS1_3_VERSION : TLS1_2_VERSION;
    if (api.SSL_CTX_set_min_proto_version(context, minimum) == 1) {
        api.SSL_CTX_clear_options(context, SSL_OP_IGNORE_UNEXPECTED_EOF);
        api.SSL_CTX_set_options(context, SSL_OP_NO_RENEGOTIATION);
        if (server) {
            api.SSL_CTX_set_default_passwd_cb(context, no_password);
            if (api.SSL_CTX_use_certificate_chain_file(context, cert) == 1 &&
                api.SSL_CTX_use_PrivateKey_file(context, key, SSL_FILETYPE_PEM) == 1 &&
                api.SSL_CTX_check_private_key(context) == 1) {
                if (alpn.length != 0) api.SSL_CTX_set_alpn_select_cb(context, select_alpn, &alpn);
                ssl = api.SSL_new(context);
            } else {
                status = server_identity;
            }
        } else {
            api.SSL_CTX_set_verify(context, SSL_VERIFY_PEER, nullptr);
            const auto trusted = *ca == '\0' ? api.SSL_CTX_set_default_verify_paths(context)
                                              : api.SSL_CTX_load_verify_locations(context, ca, nullptr);
            if (trusted == 1)
                ssl = api.SSL_new(context);
            else
                status = trust_store;
        }
    }
    if (ssl != nullptr && attach_socket(ssl, session.socket) &&
        (server || (configure_identity(ssl, host) &&
                    (alpn.length == 0 || api.SSL_set_alpn_protos(ssl, alpn.protocols, alpn.length) == 0))))
        status = 0;
    api.SSL_CTX_free(context);
    if (status == 0) return ssl;
    if (ssl != nullptr) api.SSL_free(ssl);
    return nullptr;
}

bool retryable(int32_t status) { return status == want_read || status == want_write; }

int32_t handshake_step(Session &session) {
    if (session.failed) return protocol;
    if (session.handshaken) return 0;
    auto *ssl = session.ssl;
    const auto result = run_tls(ssl, [&] {
        return session.server ? api.SSL_accept(ssl) : api.SSL_connect(ssl);
    });
    if (retryable(result.status) && session.nonblocking) return result.status;
    int32_t status;
    if (!session.server && api.SSL_get_verify_result(ssl) != X509_V_OK) {
        status = certificate;
    } else if (result.error == SSL_ERROR_NONE) {
        const unsigned char *selected = nullptr;
        unsigned int count = 0;
        api.SSL_get0_alpn_selected(ssl, &selected, &count);
        status = session.alpn.required && count == 0 ? missing_protocol : 0;
    } else {
        status = retryable(result.status) ? ETIMEDOUT : result.status != 0 ? result.status : handshake;
    }
    session.handshaken = status == 0;
    session.failed = status != 0;
    return status;
}

template <class Operation>
size_t transfer(Session &session, int32_t &status, bool reading, Operation operation) {
    if (session.failed || !session.handshaken) {
        status = protocol;
        return 0;
    }
    size_t count = 0;
    const auto result = run_tls(session.ssl, [&] { return operation(&count); });
    if (result.error == SSL_ERROR_NONE || (reading && result.error == SSL_ERROR_ZERO_RETURN)) {
        status = 0;
        return count;
    }
    if (retryable(result.status) && session.nonblocking) {
        status = result.status;
        return 0;
    }
    if (retryable(result.status)) {
        status = ETIMEDOUT;
    } else if (reading && session.socket.eof &&
               (result.status == EPIPE || result.status == ECONNRESET ||
                (result.status == 0 && result.error == SSL_ERROR_SYSCALL))) {
        status = unexpected_eof;
    } else if (!reading && result.error == SSL_ERROR_ZERO_RETURN) {
        status = EPIPE;
    } else {
        status = result.status != 0 ? result.status : protocol;
    }
    session.failed = true;
    return 0;
}

} // namespace

extern "C" uintptr_t __vqr_tls_prepare(int32_t fd, const char *host, const char *ca,
                                        const char *cert, const char *key, const uint8_t *protocols,
                                        uint32_t size, int32_t version, int32_t required,
                                        int32_t nonblocking, int32_t *status) {
    if (pthread_once(&load_once, load_openssl) != 0 || !api.ready) {
        *status = unavailable;
        return 0;
    }
    auto *session = static_cast<Session *>(calloc(1, sizeof(Session) + size));
    if (session == nullptr) {
        *status = ENOMEM;
        return 0;
    }
    auto *owned_protocols = reinterpret_cast<uint8_t *>(session + 1);
    if (size != 0) memcpy(owned_protocols, protocols, size);
    session->alpn = {owned_protocols, size, required != 0};
    session->socket = {fd, false};
    session->server = *cert != '\0';
    session->nonblocking = nonblocking != 0;
    session->ssl = prepare_tls(*session, host, ca, cert, key, version, *status);
    if (session->ssl == nullptr) {
        free(session);
        return 0;
    }
    return reinterpret_cast<uintptr_t>(session);
}

extern "C" int32_t __vqr_tls_handshake(uintptr_t handle) {
    return handshake_step(*reinterpret_cast<Session *>(handle));
}

extern "C" void __vqr_tls_close(uintptr_t handle);

extern "C" uintptr_t __vqr_tls_open(int32_t fd, const char *host, const char *ca,
                                     const char *cert, const char *key, const uint8_t *protocols,
                                     uint32_t size, int32_t version, int32_t required, int32_t *status) {
    const auto handle = __vqr_tls_prepare(fd, host, ca, cert, key, protocols, size, version, required, 0, status);
    if (handle == 0) return 0;
    *status = __vqr_tls_handshake(handle);
    if (*status == 0) return handle;
    __vqr_tls_close(handle);
    return 0;
}

extern "C" size_t __vqr_tls_alpn(uintptr_t handle, uint8_t *dst) {
    const auto &session = *reinterpret_cast<const Session *>(handle);
    const unsigned char *selected = nullptr;
    unsigned int count = 0;
    api.SSL_get0_alpn_selected(session.ssl, &selected, &count);
    if (count != 0) memcpy(dst, selected, count);
    return count;
}

extern "C" size_t __vqr_tls_read(uintptr_t handle, uint8_t *data, size_t length, int32_t *status) {
    auto &session = *reinterpret_cast<Session *>(handle);
    return transfer(session, *status, true,
                    [&](size_t *count) { return api.SSL_read_ex(session.ssl, data, length, count); });
}

extern "C" size_t __vqr_tls_write(uintptr_t handle, const uint8_t *data, size_t length, int32_t *status) {
    auto &session = *reinterpret_cast<Session *>(handle);
    return transfer(session, *status, false,
                    [&](size_t *count) { return api.SSL_write_ex(session.ssl, data, length, count); });
}

extern "C" void __vqr_tls_close(uintptr_t handle) {
    auto *session = reinterpret_cast<Session *>(handle);
    if (session->handshaken && !session->failed) {
        // Send close_notify once without waiting for a reply.
        api.ERR_clear_error();
        api.SSL_shutdown(session->ssl);
    }
    api.SSL_free(session->ssl);
    free(session);
}

#else

extern "C" uintptr_t __vqr_tls_prepare(int32_t, const char *, const char *, const char *, const char *,
                                        const uint8_t *, uint32_t, int32_t, int32_t, int32_t, int32_t *status) {
    *status = unavailable;
    return 0;
}
extern "C" int32_t __vqr_tls_handshake(uintptr_t) { return unavailable; }
extern "C" uintptr_t __vqr_tls_open(int32_t, const char *, const char *, const char *, const char *,
                                     const uint8_t *, uint32_t, int32_t, int32_t, int32_t *status) {
    *status = unavailable;
    return 0;
}
extern "C" size_t __vqr_tls_alpn(uintptr_t, uint8_t *) { return 0; }
extern "C" size_t __vqr_tls_read(uintptr_t, uint8_t *, size_t, int32_t *status) {
    *status = unavailable;
    return 0;
}
extern "C" size_t __vqr_tls_write(uintptr_t, const uint8_t *, size_t, int32_t *status) {
    *status = unavailable;
    return 0;
}
extern "C" void __vqr_tls_close(uintptr_t) {}

#endif
