"""sotto: the AJSTARS living wallpaper.

  python -m sotto collect          refresh state.json (only rewrites on change)
  python -m sotto wall             run the live wallpaper (inside kitty's panel)
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
        state = collect.collect()
        from . import agents
        live = state.get("agents") if os.environ.get("SOTTO_DEMO") else agents.sessions()
        grid, _ = build(state, 195, 54, hover=None, agents=live)
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
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
