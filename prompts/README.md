# Model prompts

`opencode-default-codex.md` is the active prompt. It is a Codex-tool-compatible port of OpenCode's default prompt for models that do not match one of OpenCode's model-specific routes. The port retains OpenCode's concise, tool-oriented, iterative workflow while replacing unavailable OpenCode tool names with `exec_command` and `apply_patch`. It also explicitly prohibits shell-based file writes because this deployment previously exposed only the shell tool.

`opencode-default-upstream.md` is an unmodified snapshot retrieved on 2026-08-16 from:

https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/prompt/default.txt

OpenCode routes Qwen models to that default prompt in:

https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/system.ts

`codex-local-previous.md` is the exact prompt used by this deployment before the OpenCode-derived prompt was installed.

Codex 0.147 exposes `apply_patch` as a Responses custom tool, while the pinned llama.cpp Responses compatibility layer accepts only function tools. The local provider translates that one tool to function form for llama.cpp and restores Codex's custom-tool call protocol on the return path.

The OpenCode snapshot and derived prompt are distributed under OpenCode's MIT license in `opencode-LICENSE.txt`.
