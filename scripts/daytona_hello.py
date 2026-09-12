"""Official Daytona smoke test: create an ephemeral sandbox, run Hello World, delete it."""

from daytona import CreateSandboxFromSnapshotParams, Daytona


def main() -> None:
    daytona = Daytona()
    sandbox = None
    try:
        sandbox = daytona.create(
            CreateSandboxFromSnapshotParams(
                ephemeral=True,
                auto_stop_interval=5,
                labels={"project": "suvidha-hackathon", "purpose": "onboard-smoke"},
            )
        )
        print(f"sandbox_id={sandbox.id}")
        print(f"sandbox_state={getattr(sandbox, 'state', None)}")
        response = sandbox.process.code_run('print("Hello World")')
        print(f"result={response.result!r}")
        if getattr(response, "exit_code", 0) not in (0, None):
            raise SystemExit(f"code_run failed: exit_code={response.exit_code}")
    finally:
        if sandbox is not None:
            sandbox.delete()
            print("sandbox_deleted=true")


if __name__ == "__main__":
    main()
