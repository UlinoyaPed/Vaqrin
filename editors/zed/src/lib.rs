use zed_extension_api as zed;

struct Vaqrin;

impl zed::Extension for Vaqrin {
    fn new() -> Self {
        Self
    }

    fn language_server_command(
        &mut self,
        _: &zed::LanguageServerId,
        worktree: &zed::Worktree,
    ) -> zed::Result<zed::Command> {
        let (python, mut args) = if let Some(path) = worktree.which("python3") {
            (path, vec![])
        } else if let Some(path) = worktree.which("py") {
            (path, vec!["-3".into()])
        } else {
            (
                worktree.which("python").ok_or("Vaqrin LSP requires Python 3")?,
                vec![],
            )
        };
        let mut env = worktree.shell_env();
        if let Some(compiler) = worktree.which("vaqc") {
            env.push(("VAQRIN_PATH_COMPILER".into(), compiler));
        }
        args.extend([
            "-I".into(),
            "-c".into(),
            include_str!("server.py").into(),
            worktree.root_path(),
        ]);
        Ok(zed::Command { command: python, args, env })
    }
}

zed::register_extension!(Vaqrin);
