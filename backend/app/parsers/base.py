from abc import ABC, abstractmethod

from app.models.schemas import FileCategory, ParsedDocument


class BaseParser(ABC):
    category: FileCategory

    @abstractmethod
    async def parse(self, file_path: str) -> ParsedDocument:
        ...
