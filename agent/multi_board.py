"""Multi-board / system design support.

Allows designing multiple interconnected boards as a system.
Each board is independent but shares connector interfaces.
"""

from typing import List, Optional, Dict


class BoardDefinition:
    """A single board within a multi-board system."""

    def __init__(
        self,
        name: str,
        description: str = "",
        board_id: str = "",
    ):
        self.name = name
        self.description = description
        self.board_id = board_id or name.lower().replace(" ", "_")
        self.components: List[dict] = []
        self.nets: List[dict] = []
        self.connectors: List[dict] = []  # Board-to-board connectors
        self.board_model: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "board_id": self.board_id,
            "components": self.components,
            "nets": self.nets,
            "connectors": self.connectors,
            "board_model": self.board_model,
        }


class SystemDefinition:
    """A multi-board system with interconnections."""

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.boards: Dict[str, BoardDefinition] = {}
        self.interconnections: List[dict] = []  # Board-to-board net mappings

    def add_board(self, board: BoardDefinition):
        """Add a board to the system."""
        self.boards[board.board_id] = board

    def remove_board(self, board_id: str):
        """Remove a board from the system."""
        self.boards.pop(board_id, None)
        # Remove interconnections involving this board
        self.interconnections = [
            ic for ic in self.interconnections
            if ic.get("board_a") != board_id and ic.get("board_b") != board_id
        ]

    def add_interconnection(
        self,
        board_a: str,
        connector_a: str,
        board_b: str,
        connector_b: str,
        nets: List[dict],
    ):
        """Define a connection between two boards via their connectors."""
        self.interconnections.append({
            "board_a": board_a,
            "connector_a": connector_a,
            "board_b": board_b,
            "connector_b": connector_b,
            "nets": nets,
        })

    def get_board(self, board_id: str) -> Optional[BoardDefinition]:
        return self.boards.get(board_id)

    def list_boards(self) -> List[dict]:
        return [b.to_dict() for b in self.boards.values()]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "boards": {bid: b.to_dict() for bid, b in self.boards.items()},
            "interconnections": self.interconnections,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SystemDefinition":
        system = cls(
            name=data.get("name", "Untitled System"),
            description=data.get("description", ""),
        )
        for bid, bdata in data.get("boards", {}).items():
            board = BoardDefinition(
                name=bdata.get("name", bid),
                description=bdata.get("description", ""),
                board_id=bid,
            )
            board.components = bdata.get("components", [])
            board.nets = bdata.get("nets", [])
            board.connectors = bdata.get("connectors", [])
            board.board_model = bdata.get("board_model")
            system.add_board(board)
        system.interconnections = data.get("interconnections", [])
        return system


def create_system_from_prompt(prompt: str) -> SystemDefinition:
    """Create a system definition from a user prompt.

    Parses the prompt to identify if multiple boards are needed.
    """
    system = SystemDefinition(
        name=_extract_system_name(prompt),
        description=prompt,
    )

    # Simple heuristic: if prompt mentions "board" multiple times or
    # "main" + "daughter"/"satellite"/"sensor board", create multi-board
    prompt_lower = prompt.lower()
    board_keywords = ["main board", "daughter board", "satellite board",
                      "sensor board", "display board", "power board",
                      "controller board", "radio board"]

    boards_found = []
    for kw in board_keywords:
        if kw in prompt_lower:
            boards_found.append(kw.replace(" board", "").title() + " Board")

    if len(boards_found) >= 2:
        for board_name in boards_found:
            board = BoardDefinition(
                name=board_name,
                description=f"{board_name} for {system.name}",
            )
            system.add_board(board)
    else:
        # Default: single board system
        system.add_board(BoardDefinition(
            name="Main Board",
            description=f"Main board for {system.name}",
        ))

    return system


def _extract_system_name(prompt: str) -> str:
    """Extract a system name from the prompt."""
    # Simple extraction: first N words or the whole thing if short
    words = prompt.split()[:6]
    return " ".join(words).title()
