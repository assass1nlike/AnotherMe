import webbrowser

import uvicorn

from . import config


def main():
    port = config.load().port
    webbrowser.open(f"http://127.0.0.1:{port}")
    uvicorn.run("workbench.server:app", host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
