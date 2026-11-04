"""sotto: a living, interactive terminal wallpaper.

  python -m sotto wall             run the live wallpaper (inside kitty's panel: Linux, macOS)
  python -m sotto host             Windows: the wallpaper behind your desktop icons (needs pywebview)
  python -m sotto web [--port N]   the wallpaper in your browser, on any OS (a quick way to try it)

  python -m sotto config init      write a commented ~/.config/sotto/config.toml to start from
  python -m sotto config check     list what's wrong in your config (exit 1 if anything)
  python -m sotto config path      where the config lives
  python -m sotto config show      the settings in use, with every default filled in
  python -m sotto config get bg    one value (a colour or font_size), for launch scripts
  python -m sotto themes           the built-in themes and their colours
  python -m sotto skill install    add the "customize sotto" skill to Claude Code (~/.claude/skills)

  python -m sotto collect          refresh state.json (only rewrites on change)
  python -m sotto say "text"       make the mascot say something
  python -m sotto profile [secs]   record the running wall's frames (default 30 s) to ~/.cache/sotto/profile-*.json
  python -m sotto preview out.png  render one frame to a PNG
  python -m sotto ask "open youtube" [--do]   what the wallpaper assistant would do
  python -m sotto bootsplash DIR [--preview out.png]   render the boot screen theme into DIR
  python -m sotto greet [--demo]   the login screen (under greetd; --demo: password "sotto")
"""
import json
import os
import sys

from . import collect


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "wall"
    if cmd == "collect":
        changed = collect.write(collect.collect())
        print("state.json updated" if changed else "no changes")
        return 0
    if cmd == "say":
        collect.say(" ".join(argv[1:]) or "...")
        return 0
    if cmd == "profile":
        from . import profile

        return profile.request(argv[1:])
    if cmd == "preview":
        from .art import font_path
        from .grid import to_png
        from .scene import build

        out = argv[1] if len(argv) > 1 else "preview.png"
        from . import agents
        from . import config as configmod
        from . import widgets
        from .grid import set_palette

        settings = configmod.load()
        set_palette(settings["palette"])
        if os.environ.get("SOTTO_DEMO") and os.environ.get("SOTTO_STATE"):
            state = json.loads(open(os.environ["SOTTO_STATE"], encoding="utf-8").read())
        else:
            state = collect.collect()
        live = state.get("agents") if os.environ.get("SOTTO_DEMO") else agents.sessions()
        cols, rows = 195, 54
        if "--size" in argv:
            i = argv.index("--size")
            cols, rows = (int(v) for v in argv[i + 1].lower().split("x"))
        grid, _ = build(state, cols, rows, hover=None, agents=live, settings=settings,
                        widget_rows=widgets.run_all(settings["widgets"]))
        to_png(grid, out, font_path())
        print(f"wrote {out}")
        return 0
    if cmd == "ask":
        from . import assistant

        do = "--do" in argv
        text = " ".join(a for a in argv[1:] if a != "--do")
        r = assistant.handle(text)
        if r is None:
            print("(asking Claude)")
            r = assistant.ask(text)
        print(r["reply"])
        if r["action"]:
            print("action:", r["action"])
            if do:
                assistant.run_action(r["action"])
        return 0
    if cmd == "greet":
        from . import greeter

        return greeter.run(argv[1:])
    if cmd == "bootsplash":
        from pathlib import Path

        from . import bootsplash

        if "--preview" in argv:
            i = argv.index("--preview")
            print(f"wrote {bootsplash.preview(Path(argv[i + 1] if len(argv) > i + 1 else 'bootsplash.png'))}")
            argv = argv[:i] + argv[i + 2:]
        if len(argv) > 1:
            for f in bootsplash.render(Path(argv[1])):
                print(f"wrote {f}")
        return 0
    if cmd == "dump":
        print(json.dumps(collect.collect(), indent=1))
        return 0
    if cmd == "wall":
        from .wall import run

        return run()
    if cmd == "web":
        from . import web

        return web.main(argv[1:])
    if cmd == "host":
        from . import plat

        if plat.OS != "windows" and "--force" not in argv:
            print("`sotto host` is the Windows wallpaper. On Linux and macOS run bin/sotto-wall "
                  "(kitty's background panel); anywhere, `python -m sotto web` shows it in a browser.")
            return 2
        from . import host_windows

        return host_windows.main(argv[1:])
    if cmd == "config":
        return config_cmd(argv[1:])
    if cmd == "themes":
        from . import config as configmod

        cur = configmod.raw().get("theme", "fresco")
        for name, pal in configmod.THEMES.items():
            mark = "*" if name == cur else " "
            print(f"{mark} {name:8} " + "  ".join(f"{k}={v}" for k, v in pal.items()))
        print("\nSet one with  theme = \"night\"  in", configmod.PATH)
        return 0
    if cmd == "skill":
        return skill_cmd(argv[1:])
    print(__doc__)
    return 2


def config_cmd(argv: list[str]) -> int:
    from . import config as configmod

    sub = argv[0] if argv else "check"
    if sub == "path":
        print(configmod.PATH)
        return 0
    if sub == "init":
        import getpass

        name = os.environ.get("SOTTO_NAME") or getpass.getuser()
        path, wrote = configmod.init(name, force="--force" in argv)
        print(f"wrote {path}" if wrote else f"{path} already exists (add --force to replace it)")
        return 0
    if sub == "get":
        # for launch scripts: `config get bg` -> #ece2d0, `config get font_size` -> 11 (empty when unset)
        key = argv[1] if len(argv) > 1 else ""
        st = configmod.load()
        if key in st["palette"]:
            print(st["palette"][key])
        elif key == "font_size":
            fs = st["display"]["font_size"]
            print("" if fs is None else (int(fs) if float(fs).is_integer() else fs))
        else:
            print(f"unknown key {key!r}: a colour ({', '.join(configmod.COLOR_KEYS)}) or font_size")
            return 2
        return 0
    if sub == "show":
        print(json.dumps(configmod.load(), indent=1, ensure_ascii=False))
        return 0
    if sub == "check":
        if not configmod.PATH.exists():
            print(f"no config yet at {configmod.PATH}: the defaults are in use. `python -m sotto config init` writes one.")
            return 0
        probs = configmod.check()
        for pr in probs:
            print("·", pr)
        print(f"{configmod.PATH}: " + (f"{len(probs)} problem(s)" if probs else "looks good"))
        return 1 if probs else 0
    print(__doc__)
    return 2


def skill_cmd(argv: list[str]) -> int:
    import shutil
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "skills" / "sotto-customize"
    if not src.is_dir():
        src = Path(__file__).resolve().parent / "skills" / "sotto-customize"
    if (argv[:1] or ["install"])[0] != "install" or not src.is_dir():
        print("usage: python -m sotto skill install   (copies skills/sotto-customize to ~/.claude/skills/)")
        return 2
    dst = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "skills" / "sotto-customize"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)
    print(f"installed {dst}\nIn Claude Code, ask: \"customize my sotto wallpaper\" (or type /sotto-customize).")
    return 0


def cli() -> None:
    """The `sotto` command, when installed with uv tool / pipx."""
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    cli()
