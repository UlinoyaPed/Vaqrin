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
        let python = worktree.which("python3").ok_or("Vaqrin LSP requires Python 3")?;
        let mut env = worktree.shell_env();
        if let Some(compiler) = worktree.which("vaqc") {
            env.push(("VAQRIN_PATH_COMPILER".into(), compiler));
        }
        Ok(zed::Command {
            command: python,
            args: vec![
                "-I".into(),
                "-c".into(),
                include_str!("server.py").into(),
                worktree.root_path(),
            ],
            env,
        })
    }
}

zed::register_extension!(Vaqrin);
