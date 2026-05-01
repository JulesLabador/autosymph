"""Wizard exception types.

The runner (``runner.py``) catches each at the top level and produces a
specific exit message + exit code. Step functions raise these instead of
returning sentinel values so the runner can centralize the recovery logic.
"""
from __future__ import annotations


class WizardError(Exception):
    """Base class for wizard-specific exceptions."""


class WizardAlreadyConfigured(WizardError):
    """Existing config detected — wizard refuses to run.

    ``trigger_path`` names the specific filesystem path that tripped the check
    so the runner can include it in the user-facing guidance.
    """

    def __init__(self, trigger_path: str, reason: str) -> None:
        super().__init__(f"Existing config detected at {trigger_path}: {reason}")
        self.trigger_path = trigger_path
        self.reason = reason


class WizardAborted(WizardError):
    """User chose to abort at a confirmation prompt.

    Carries the step name so logs can show where the abort happened. No
    user-config writes have occurred when this is raised.
    """

    def __init__(self, step: str) -> None:
        super().__init__(f"Wizard aborted at step {step!r}")
        self.step = step


class WizardLinearMutationFailed(WizardError):
    """Linear API mutation failed mid-bundle.

    ``succeeded_states`` lists the canonical names of states that were
    successfully created before the failure. The runner uses this list to
    print recovery guidance and the user can locate the partial state in the
    ``wizard-mutations.log`` file.
    """

    def __init__(self, failed_state: str, succeeded_states: list[str], cause: str) -> None:
        super().__init__(
            f"Linear mutation failed on state {failed_state!r}: {cause} "
            f"(succeeded so far: {succeeded_states})"
        )
        self.failed_state = failed_state
        self.succeeded_states = succeeded_states
        self.cause = cause


class WizardPromptsRootMissing(WizardError):
    """The autosymph package's prompts/ directory is missing or incomplete.

    Indicates the package was installed without source (e.g. wheel-only
    install). Wizard exits before any Linear or filesystem changes per AC13.
    """

    def __init__(self, resolved_path: str, missing_files: list[str]) -> None:
        super().__init__(
            f"autosymph prompts/ directory at {resolved_path} is missing required "
            f"files: {missing_files}. Install autosymph from source."
        )
        self.resolved_path = resolved_path
        self.missing_files = missing_files
