"""Account Manager for loading test configurations and distributing student accounts to slaves."""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from shared.models import StudentAccount, WorkerInfo

logger = logging.getLogger("master.account_manager")

DEFAULT_TEST_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "test-config"


class AccountManager:
    def __init__(self, config_dir: Optional[Path] = None):
        env_dir = os.getenv("TEST_CONFIG_DIR")
        if env_dir:
            self.config_dir = Path(env_dir).resolve()
        elif config_dir:
            self.config_dir = Path(config_dir).resolve()
        else:
            self.config_dir = DEFAULT_TEST_CONFIG_DIR

    def list_available_exams(self) -> List[Dict[str, Any]]:
        exams = []
        if not self.config_dir.exists():
            logger.warning(f"test-config directory not found at {self.config_dir}")
            return exams

        for item in sorted(self.config_dir.iterdir()):
            if item.is_dir() and (item / "config.json").exists():
                exam_id = item.name
                config_path = item / "config.json"
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    
                    accessible_users = data.get("accessibleUsers", [])
                    sections = data.get("sections", [])
                    total_puzzles = sum(len(s.get("puzzles", [])) for s in sections)

                    # List available solutions
                    solutions_dir = item / "solutions"
                    solutions_count = 0
                    if solutions_dir.exists():
                        solutions_count = len(list(solutions_dir.glob("*.py")) + list(solutions_dir.glob("*.c")) + list(solutions_dir.glob("*.cpp")))

                    exams.append({
                        "exam_id": exam_id,
                        "title": data.get("testTitle", exam_id),
                        "description": data.get("description", ""),
                        "total_users": len(accessible_users),
                        "total_puzzles": total_puzzles,
                        "solutions_count": solutions_count,
                    })
                except Exception as e:
                    logger.error(f"Error loading exam config from {config_path}: {e}")

        return exams

    def get_exam_details(self, exam_id: str) -> Optional[Dict[str, Any]]:
        exam_dir = self.config_dir / exam_id
        config_path = exam_dir / "config.json"
        if not config_path.exists():
            return None

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Load solution code snippets
            solutions = {}
            solutions_dir = exam_dir / "solutions"
            if solutions_dir.exists():
                for sol_file in solutions_dir.iterdir():
                    if sol_file.is_file() and not sol_file.name.startswith("."):
                        try:
                            with open(sol_file, "r", encoding="utf-8") as sf:
                                solutions[sol_file.stem] = sf.read()
                        except Exception:
                            pass

            return {
                "exam_id": exam_id,
                "title": data.get("testTitle", exam_id),
                "description": data.get("description", ""),
                "judgerSettings": data.get("judgerSettings", {}),
                "accessibleUsers": data.get("accessibleUsers", []),
                "sections": data.get("sections", []),
                "solutions": solutions,
            }
        except Exception as e:
            logger.error(f"Failed to read exam details for {exam_id}: {e}")
            return None

    def distribute_accounts(
        self, exam_id: str, workers: List[WorkerInfo], accounts_per_worker: int = 1
    ) -> Dict[str, List[StudentAccount]]:
        """Distribute student accounts from accessibleUsers across the given workers."""
        details = self.get_exam_details(exam_id)
        if not details:
            logger.error(f"Cannot distribute accounts: exam {exam_id} not found")
            return {w.worker_id: [] for w in workers}

        raw_users = details.get("accessibleUsers", [])
        # Filter out 'admin' if present
        student_users = [u for u in raw_users if u.get("id") not in ("admin", "teacher")]

        distribution: Dict[str, List[StudentAccount]] = {w.worker_id: [] for w in workers}
        if not workers:
            return distribution

        account_idx = 0
        total_needed = len(workers) * accounts_per_worker

        for i in range(total_needed):
            worker = workers[i % len(workers)]
            if account_idx < len(student_users):
                u = student_users[account_idx]
                distribution[worker.worker_id].append(
                    StudentAccount(
                        test_id=str(u.get("id")),
                        name=u.get("name"),
                        ip=u.get("ip"),
                    )
                )
                account_idx += 1
            else:
                # If there are not enough accounts in accessibleUsers, generate virtual student account
                fake_id = f"student_{account_idx + 1:04d}"
                distribution[worker.worker_id].append(
                    StudentAccount(
                        test_id=fake_id,
                        name=f"Virtual Student {account_idx + 1}",
                        ip=f"10.0.0.{account_idx + 2}",
                    )
                )
                account_idx += 1

        return distribution
