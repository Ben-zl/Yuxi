"""Unity Profiler Analyzer - Markdown Formatter

Utility functions for formatting Markdown output
"""

from typing import Any, List, Dict

# Handle both relative and absolute imports
try:
    from ..utils.helpers import format_number, format_percentage, format_time
except (ImportError, ValueError):
    from utils.helpers import format_number, format_percentage, format_time


class MarkdownFormatter:
    """Markdown formatter for generating reports"""

    @staticmethod
    def h1(text: str) -> str:
        """Format as H1 heading"""
        return f"# {text}"

    @staticmethod
    def h2(text: str) -> str:
        """Format as H2 heading"""
        return f"## {text}"

    @staticmethod
    def h3(text: str) -> str:
        """Format as H3 heading"""
        return f"### {text}"

    @staticmethod
    def table(
        headers: List[str],
        rows: List[List[str]],
        align: List[str] = None
    ) -> str:
        """Format a table in Markdown

        Args:
            headers: Table headers
            rows: Table rows (list of lists)
            align: Column alignment (left/right/center)

        Returns:
            Markdown table string
        """
        if not rows:
            return ""

        # Default alignment to left
        if align is None:
            align = ["left"] * len(headers)

        # Build table
        lines = []

        # Header row
        header_row = "| " + " | ".join(headers) + " |"
        lines.append(header_row)

        # Separator row
        separators = []
        for a in align:
            if a == "left":
                separators.append("---")
            elif a == "right":
                separators.append("---:")
            elif a == "center":
                separators.append(":---:")
            else:
                separators.append("---")
        separator_row = "| " + " | ".join(separators) + " |"
        lines.append(separator_row)

        # Data rows
        for row in rows:
            # Ensure row has same number of columns as headers
            if len(row) < len(headers):
                row = row + [""] * (len(headers) - len(row))
            data_row = "| " + " | ".join(str(cell) for cell in row) + " |"
            lines.append(data_row)

        return "\n".join(lines)

    @staticmethod
    def metric_table(
        data: Dict[str, Any],
        label_func=None
    ) -> str:
        """Format metrics as a table

        Args:
            data: Dictionary of metrics
            label_func: Optional function to transform labels

        Returns:
            Markdown table string
        """
        if label_func is None:
            label_func = lambda x: x.replace("_", " ").title()

        headers = ["Metric", "Value", "Status"]
        rows = []

        for key, value in data.items():
            label = label_func(key)
            value_str = format_number(value) if isinstance(value, (int, float)) else str(value)
            status = ""  # Could add status evaluation

            rows.append([label, value_str, status])

        return MarkdownFormatter.table(headers, rows)

    @staticmethod
    def code_block(code: str, language: str = "") -> str:
        """Format as code block

        Args:
            code: Code content
            language: Programming language

        Returns:
            Markdown code block string
        """
        lang_suffix = f"{language}" if language else ""
        return f"```{lang_suffix}\n{code}\n```"

    @staticmethod
    def list_items(items: List[str], ordered: bool = False) -> str:
        """Format items as a list

        Args:
            items: List of items
            ordered: Whether to use ordered list

        Returns:
            Markdown list string
        """
        if ordered:
            return "\n".join(f"{i+1}. {item}" for i, item in enumerate(items))
        else:
            return "\n".join(f"- {item}" for item in items)

    @staticmethod
    def status_badge(status: str, icon: str = "") -> str:
        """Format a status badge

        Args:
            status: Status text
            icon: Icon emoji

        Returns:
            Formatted status string
        """
        return f"{icon} {status}" if icon else status

    @staticmethod
    def section(title: str, content: str) -> str:
        """Format a section

        Args:
            title: Section title
            content: Section content

        Returns:
            Formatted section string
        """
        return f"{MarkdownFormatter.h2(title)}\n\n{content}"

    @staticmethod
    def subsection(title: str, content: str) -> str:
        """Format a subsection

        Args:
            title: Subsection title
            content: Subsection content

        Returns:
            Formatted subsection string
        """
        return f"{MarkdownFormatter.h3(title)}\n\n{content}"

    @staticmethod
    def bold(text: str) -> str:
        """Format text as bold

        Args:
            text: Text to format

        Returns:
            Bold formatted text
        """
        return f"**{text}**"

    @staticmethod
    def code(text: str) -> str:
        """Format text as inline code

        Args:
            text: Text to format

        Returns:
            Inline code formatted text
        """
        return f"`{text}`"

    @staticmethod
    def format_number(value: Any, decimals: int = 2) -> str:
        """Format a number with specified decimals

        Args:
            value: Number to format
            decimals: Number of decimal places

        Returns:
            Formatted number string
        """
        return format_number(value, decimals)

    @staticmethod
    def format_percentage(value: Any, decimals: int = 1) -> str:
        """Format a value as percentage

        Args:
            value: Value to format (0-100 or 0-1)
            decimals: Number of decimal places

        Returns:
            Formatted percentage string
        """
        return format_percentage(value, decimals)

    @staticmethod
    def format_time(value: Any, unit: str = "ms") -> str:
        """Format a time value

        Args:
            value: Time value
            unit: Time unit (ms, s, etc.)

        Returns:
            Formatted time string
        """
        return format_time(value, unit)
