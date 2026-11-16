import os
from dataclasses import dataclass
from pathlib import Path


class Conflict(Exception):
    pass


class Missing(Exception):
    pass


@dataclass(frozen=True)
class Workspace:
    path: str
    identity: str

    @classmethod
    def inspect(cls, root: Path):
        try:
            resolved = root.resolve(strict=True)
            stat = resolved.stat()
            if not resolved.is_dir() or not stat.st_ino:
                raise OSError("工作区没有可验证的目录身份")
        except (OSError, RuntimeError) as error:
            raise Conflict("工作区缺失或无法验证，请恢复原目录后再试。") from error
        return cls(os.path.normcase(str(resolved)), f"{stat.st_dev}:{stat.st_ino}")

    def verify(self, root: Path):
        if self.inspect(root) != self:
            raise Conflict("当前工作区与会话绑定的目录不匹配，请恢复原工作区。")
