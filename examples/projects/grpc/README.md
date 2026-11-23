# gRPC Greeter

服务端通过 TLS 在回环地址提供一个 `SayHello` 方法。客户端发送名字 `Vaqrin` 并打印问候语。
示例在 GNU/Linux 上使用 OpenSSL 3、nghttp2 和 zlib 的开发文件。

## 构建和运行

从仓库根目录构建。项目已包含 [pb_example](pb_example/greeter_pb.vqr)，单文件构建和 Zed 的
`run code` 都能直接解析这个包。

```sh
project=examples/projects/grpc
output="$PWD/build/examples/greeter"
mkdir -p "$output"
build/host/vaqc build "$project/server/main.vqr" -o "$output/server"
build/host/vaqc build "$project/client/main.vqr" -o "$output/client"

openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=localhost \
  -addext subjectAltName=DNS:localhost,IP:127.0.0.1 \
  -keyout "$output/key.pem" -out "$output/cert.pem"

VAQRIN_GRPC_CERT="$output/cert.pem" VAQRIN_GRPC_KEY="$output/key.pem" "$output/server"
```

服务端打印监听端口后，在另一个终端运行客户端。

```sh
output="$PWD/build/examples/greeter"
VAQRIN_GRPC_CERT="$output/cert.pem" "$output/client"
```

客户端输出 `Hello, Vaqrin`。连接关闭后，示例服务端退出。端口默认为 50051。
可在两端设置相同的 `VAQRIN_GRPC_PORT`。为服务端设置 `0` 时，使用它打印的实际端口启动客户端。

## 重新生成绑定

修改 [greeter.proto](greeter.proto) 后，用 `protoc` 和 Python 的 `protobuf` 模块从仓库根目录重新生成。

```sh
protoc -I examples/projects/grpc \
  --plugin=protoc-gen-vaqrin=compiler/tools/protoc-gen-vaqrin \
  --vaqrin_out=examples/projects/grpc greeter.proto
```
