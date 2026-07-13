from dataclasses import dataclass


@dataclass(frozen=True)
class PromptTemplate:
    """Шаблон системного промпта с подстановкой {{переменных}}."""

    text: str

    def render(self, variables: dict[str, object]) -> str:
        rendered = self.text
        for key, value in variables.items():
            rendered = rendered.replace("{{" + key + "}}", str(value))
        return rendered
