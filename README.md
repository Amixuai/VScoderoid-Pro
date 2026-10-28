<p align="center">
  <img src="docs/logo/vscodroid.png" alt="VSCodroid Logo" width="150">
</p>

<h1 align="center">VSCodroid</h1>

<p align="center">
  <strong>VS Code, natively on Android.</strong><br>
  A full-featured IDE on your phone: real editor, real terminal, real tooling.
</p>

---

## About

VSCodroid runs a local VS Code Server on your Android device. The VS Code Web Client renders inside a WebView, and the server (Node.js) runs as a native ARM64 process, all on `localhost`. Everything works offline.

## Features

- **Real VS Code**: Monaco Editor, Workbench UI, Command Palette and the features you already know.
- **Extension support**: install themes, linters and language support from [Open VSX](https://open-vsx.org).
- **Integrated terminal**: full bash with real PTY support (tmux, line editing, Node and Python REPLs).
- **Batteries included**: Node.js, Python 3, Git, npm, SSH and essential tools bundled out of the box.
- **Language picker**: Ruby, Java and C/C++ install on demand.
- **Mobile-optimized**: extra key row (Ctrl, Alt, Tab, Esc, F1-F12, symbols), touch-friendly UI, clipboard bridge.
- **Dev server preview**: preview a running dev server beside your code.
- **Multilingual UI**: follows the phone's language.

## Requirements

| Requirement  | Minimum               |
| ------------ | --------------------- |
| Android      | 11 (API 30)           |
| Architecture | arm64-v8a             |
| WebView      | Chrome 125+           |
| RAM          | 4 GB recommended      |
| Storage      | ~738 MB free          |

## Building from Source

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full build steps and the order of the build scripts.

```bash
git clone https://github.com/Amixuai/Vscoderoid-Pro.git
cd Vscoderoid-Pro/android && ./gradlew assembleDebug
```

## Credits

**Original developer and creator of the foundation of this project:**
[**Yudhi Armyndharis**](https://github.com/rmyndharis) and the VSCodroid Community, authors of the original [VSCodroid](https://github.com/rmyndharis/VSCodroid). This repository ([Vscoderoid-Pro](https://github.com/Amixuai/Vscoderoid-Pro), maintained by [Amixuai](https://github.com/Amixuai)) is built on their work.

**Built with the help of AI assistants:**

<p>
  <a href="https://www.anthropic.com/claude"><img src="https://img.shields.io/badge/Claude-Anthropic-D97757?style=for-the-badge&logo=anthropic&logoColor=white" alt="Claude by Anthropic"></a>
  <a href="https://chatgpt.com"><img src="https://img.shields.io/badge/ChatGPT-OpenAI-10A37F?style=for-the-badge&logo=openai&logoColor=white" alt="ChatGPT by OpenAI"></a>
  <a href="https://gemini.google.com"><img src="https://img.shields.io/badge/Gemini-Google-4285F4?style=for-the-badge&logo=googlegemini&logoColor=white" alt="Gemini by Google"></a>
</p>

Third-party software bundled with this project is listed in [NOTICE.md](NOTICE.md).

## Legal

This project is **not affiliated with or endorsed by Microsoft Corporation**. "Visual Studio Code" and "VS Code" are trademarks of Microsoft. The bundled editor server is built from the MIT-licensed [Code - OSS](https://github.com/microsoft/vscode) source and uses the [Open VSX](https://open-vsx.org) registry.

## License

Released under the **MIT License**. See [LICENSE](LICENSE). If you build on this repository, you must keep the copyright notices in [LICENSE](LICENSE), which credit both the original developer and this repository's maintainer. Bundled third-party components keep their own licences ([NOTICE.md](NOTICE.md)).
