# Greeter

Build or install `vaqc` and add it to PATH. This example uses OpenSSL 3 and
nghttp2 on GNU/Linux. Install Python's `protobuf` module for the plugin and
a standard `protoc` binary. The plugin ships beside the installed compiler.

Run these commands from the repository root.

```sh
mkdir -p build/greeter
protoc -I examples/grpc \
  --plugin=protoc-gen-vaqrin=compiler/tools/protoc-gen-vaqrin \
  --vaqrin_out=build/greeter greeter.proto
vaqc build examples/grpc/server --package-path build/greeter -o build/greeter/server
vaqc build examples/grpc/client --package-path build/greeter -o build/greeter/client
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=Greeter \
  -addext subjectAltName=DNS:localhost \
  -keyout build/greeter/key.pem -out build/greeter/cert.pem
export VAQRIN_GRPC_CERT="$PWD/build/greeter/cert.pem"
export VAQRIN_GRPC_KEY="$PWD/build/greeter/key.pem"
build/greeter/server
```

In a second terminal, set `VAQRIN_GRPC_CERT` to the same certificate path
and run `build/greeter/client`. The client prints `Hello, Vaqrin`.
The sample server handles one TLS connection on loopback port 50051 and
exits when the connection closes.
