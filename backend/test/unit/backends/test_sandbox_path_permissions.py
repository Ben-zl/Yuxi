from yuxi.agents.backends.sandbox.backend import ProvisionerSandboxBackend


def _backend(workdir_path: str) -> ProvisionerSandboxBackend:
    return ProvisionerSandboxBackend(
        "thread-1",
        uid="user-1",
        workdir_path=workdir_path,
    )


def test_managed_sandbox_writes_only_its_bound_workdir():
    backend = _backend("projects/project-1")

    assert backend._can_write_path("/home/gem/user-data/projects/project-1/uploads/file.txt")
    assert not backend._can_write_path("/home/gem/user-data/projects/project-2/uploads/file.txt")
    assert not backend._can_write_path("/home/gem/user-data/uploads/file.txt")
    assert not backend._can_write_path("/home/gem/user-data/outputs/report.md")
    assert not backend._can_write_path("/home/gem/user-data/workspace/notes.md")


def test_linked_sandbox_writes_only_its_bound_workdir():
    backend = _backend("clients/acme")

    assert backend._can_write_path("/home/gem/user-data/clients/acme/outputs/report.md")
    assert not backend._can_write_path("/home/gem/user-data/clients/other/outputs/report.md")
    assert not backend._can_write_path("/home/gem/user-data/outputs/report.md")
