use zed_extension_api as zed;

struct Vaqrin;

fn python_command(worktree: &zed::Worktree) -> Option<(String, Vec<String>)> {
    if let Some(path) = worktree.which("python3") {
        Some((path, vec![]))
    } else if let Some(path) = worktree.which("py") {
        Some((path, vec!["-3".into()]))
    } else {
        worktree.which("python").map(|path| (path, vec![]))
    }
}

fn request_kind(
    config: &zed::serde_json::Value,
) -> zed::Result<zed::StartDebuggingRequestArgumentsRequest> {
    use zed::StartDebuggingRequestArgumentsRequest::{Attach, Launch};
    match config.get("request").and_then(|value| value.as_str()) {
        Some("launch") => Ok(Launch),
        Some("attach") => Ok(Attach),
        _ => Err("Vaqrin requires a launch or attach request".into()),
    }
}

impl zed::Extension for Vaqrin {
    fn new() -> Self {
        Self
    }

    fn language_server_command(
        &mut self,
        _: &zed::LanguageServerId,
        worktree: &zed::Worktree,
    ) -> zed::Result<zed::Command> {
        let (python, mut args) = python_command(worktree).ok_or("Vaqrin LSP requires Python 3")?;
        let mut env = worktree.shell_env();
        if let Some(compiler) = worktree.which("vaqc") {
            env.push(("VAQRIN_PATH_COMPILER".into(), compiler));
        }
        args.extend([
            "-I".into(),
            "-c".into(),
            concat!(
                include_str!("debug_hover.py"),
                "\n",
                include_str!("protocol.py"),
                "\n",
                include_str!("server.py")
            )
            .into(),
            worktree.root_path(),
        ]);
        Ok(zed::Command {
            command: python,
            args,
            env,
        })
    }

    fn dap_request_kind(
        &mut self,
        _adapter: String,
        config: zed::serde_json::Value,
    ) -> zed::Result<zed::StartDebuggingRequestArgumentsRequest> {
        request_kind(&config)
    }

    fn get_dap_binary(
        &mut self,
        _adapter: String,
        task: zed::DebugTaskDefinition,
        user_binary: Option<String>,
        worktree: &zed::Worktree,
    ) -> zed::Result<zed::DebugAdapterBinary> {
        if task.tcp_connection.is_some() {
            return Err("Vaqrin uses lldb-dap over standard input and output".into());
        }
        let command = user_binary
            .or_else(|| worktree.which("lldb-dap"))
            .ok_or("Vaqrin requires lldb-dap on PATH or dap.Vaqrin.binary")?;
        let mut config: zed::serde_json::Value =
            zed::serde_json::from_str(&task.config).map_err(|error| error.to_string())?;
        let request = request_kind(&config)?;
        if matches!(request, zed::StartDebuggingRequestArgumentsRequest::Launch) {
            if config
                .get("program")
                .and_then(|value| value.as_str())
                .is_none_or(str::is_empty)
            {
                return Err("Vaqrin launch needs the path to a debug executable".into());
            }
            if config.get("cwd").is_none() {
                config["cwd"] = worktree.root_path().into();
            }
            if ["console", "runInTerminal", "stdio"]
                .iter()
                .all(|key| config.get(key).is_none())
            {
                config["runInTerminal"] = true.into();
            }
        }
        let (command, arguments) = if let Some((python, mut args)) = python_command(worktree) {
            args.extend([
                "-I".into(),
                "-c".into(),
                concat!(
                    include_str!("debug_hover.py"),
                    "\n",
                    include_str!("protocol.py"),
                    "\n",
                    include_str!("dap.py")
                )
                .into(),
                worktree.root_path(),
                command,
                "--no-lldbinit".into(),
            ]);
            (python, args)
        } else {
            (command, vec!["--no-lldbinit".into()])
        };
        Ok(zed::DebugAdapterBinary {
            command: Some(command),
            arguments,
            envs: worktree.shell_env(),
            cwd: Some(worktree.root_path()),
            connection: None,
            request_args: zed::StartDebuggingRequestArguments {
                request,
                configuration: config.to_string(),
            },
        })
    }
}

zed::register_extension!(Vaqrin);
