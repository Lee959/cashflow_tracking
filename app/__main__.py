import uvicorn

if __name__ == "__main__":
    # 127.0.0.1 only: this app holds Gmail/bank access and has no login of its own.
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True, reload_dirs=["app"])
