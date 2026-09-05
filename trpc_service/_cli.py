"""命令行入口：python -m trpc_service._cli <command>"""
import typer

app = typer.Typer(help="trpc-agent-service 命令行工具", no_args_is_help=True)


@app.command()
def version():
    """显示版本号"""
    from trpc_service.version import __version__
    typer.echo(f"trpc agent service version: {__version__}")


@app.command()
def tenants():
    """列出全部租户（直接读配置文件，不依赖服务进程）"""
    from trpc_service.config.loader import load_config  # ⚠️ 原来写成 trpc_service.loader，包路径错了
    for cfg in load_config().values():
        typer.echo(f"{cfg.tenant_id}\t{cfg.name}\t{cfg.app.app_name}\t{cfg.storage.session_backend}")


@app.command()
def serve():
    """启动服务（等价 start.sh）"""
    import uvicorn
    from trpc_service.config.settings import ServerConfig
    s = ServerConfig()
    uvicorn.run("trpc_service.web.app:app", host=s.host, port=s.port)


if __name__ == "__main__":
    app()  # ⚠️ 原来缺这个：python -m 方式执行时，没有入口调用 app() 什么都不会发生
