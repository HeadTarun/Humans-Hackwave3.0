"""Comprehensive test suite for Tree-Sitter UX False Positive Analyzer & LLM Context Engine.

Demonstrates Tree-Sitter AST inspection across 5 real-world UX drift scenarios:
  Case 1: Wording / Copy Rebrand (handler equivalence)
  Case 2: Component Library Migration (e.g. plain button -> Shadcn <Button>)
  Case 3: Icon Button Migration (text replaced by SVG + aria-label)
  Case 4: Disabled State Guard (disabled={!isFormValid})
  Case 5: Modal / Dialog Wrapping (<Dialog><DialogContent><Button ...>)
"""
import sys
from pathlib import Path
import tempfile
import pytest

# Ensure backend root is on sys.path when invoked directly as a standalone script
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.services.repository_inspector import (
    TreeSitterInspector,
    format_llm_ux_context,
    analyze_ux_false_positive,
)


@pytest.fixture
def inspector():
    insp = TreeSitterInspector()
    assert insp._ts_available, "Tree-sitter must be available for UX AST tests"
    return insp


def test_case_1_wording_copy_rebrand(inspector):
    """Case 1: Designer changed 'Place Order' to 'Complete Purchase'.

    Both buttons use identical handlePayment handler.
    Tree-Sitter detects the handler match and identifies it as a FALSE POSITIVE.
    """
    code = """
    export function CheckoutFooter() {
        return (
            <div className="footer">
                <button onClick={handlePayment} className="btn-primary">
                    Complete Purchase
                </button>
            </div>
        );
    }
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        fpath = Path(tmpdir) / "CheckoutFooter.tsx"
        fpath.write_text(code, encoding="utf-8")
        info = inspector._inspect_file(Path(tmpdir), fpath)

    assert info is not None
    ux = info["ux_elements"]
    assert len(ux) == 1
    btn = ux[0]

    # Verify AST extraction
    assert btn["tag"] == "button"
    assert btn["text"] == "Complete Purchase"
    assert btn["handler"] == "onClick={handlePayment}"
    assert btn["line"] == 5

    # Simulated failing test looking for old wording 'Place Order'
    old_test_target = {"name": "Place Order", "role": "button"}
    analysis = analyze_ux_false_positive(ux, old_test_target)

    assert analysis is not None
    assert analysis["is_false_positive"] is True
    assert analysis["confidence"] >= 0.85  # Boosted by handlePayment match
    print("\n--- CASE 1 OUTPUT ---")
    print("Extracted Element:", btn)
    print("False Positive Diagnosis:", analysis)


def test_case_2_component_library_migration(inspector):
    """Case 2: Plain <button> migrated to Shadcn/Radix <Button> with data-testid."""
    code = """
    import { Button } from "@/components/ui/button";

    export function PayButton({ total }: { total: number }) {
        return (
            <Button data-testid="checkout-cta" onClick={submitCheckout}>
                Pay ${total}
            </Button>
        );
    }
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        fpath = Path(tmpdir) / "PayButton.tsx"
        fpath.write_text(code, encoding="utf-8")
        info = inspector._inspect_file(Path(tmpdir), fpath)

    assert info is not None
    ux = info["ux_elements"]
    assert len(ux) == 1
    btn = ux[0]

    # Verify custom capitalized component tag & testid extraction
    assert btn["tag"] == "Button"
    assert btn["test_id"] == "checkout-cta"
    assert btn["handler"] == "onClick={submitCheckout}"

    # Simulated failing test looking for old generic 'Pay' button
    old_test_target = {"name": "Pay", "role": "button"}
    analysis = analyze_ux_false_positive(ux, old_test_target)

    assert analysis is not None
    assert analysis["is_false_positive"] is True
    assert analysis["suggested_selector"] == '[data-testid="checkout-cta"]'
    print("\n--- CASE 2 OUTPUT ---")
    print("Extracted Element:", btn)
    print("Suggested Selector:", analysis["suggested_selector"])


def test_case_3_icon_button_migration(inspector):
    """Case 3: Visual text removed and replaced by SVG Icon with aria-label."""
    code = """
    export function ConfirmAction() {
        return (
            <IconButton aria-label="Confirm Purchase" onClick={processOrder}>
                <CheckIcon className="w-5 h-5" />
            </IconButton>
        );
    }
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        fpath = Path(tmpdir) / "ConfirmAction.tsx"
        fpath.write_text(code, encoding="utf-8")
        info = inspector._inspect_file(Path(tmpdir), fpath)

    assert info is not None
    ux = info["ux_elements"]
    assert len(ux) == 1
    icon_btn = ux[0]

    # Verify aria-label extraction on textless icon button
    assert icon_btn["tag"] == "IconButton"
    assert icon_btn["aria_label"] == "Confirm Purchase"
    assert icon_btn["handler"] == "onClick={processOrder}"
    assert icon_btn["text"] == ""

    # Simulated test looking for 'Confirm Purchase'
    test_target = {"name": "Confirm Purchase", "role": "button"}
    analysis = analyze_ux_false_positive(ux, test_target)

    assert analysis is not None
    assert analysis["is_false_positive"] is True
    assert analysis["suggested_selector"] == '[aria-label="Confirm Purchase"]'
    print("\n--- CASE 3 OUTPUT ---")
    print("Extracted Element:", icon_btn)
    print("ARIA Selector Recovered:", analysis["suggested_selector"])


def test_case_4_disabled_state_guard(inspector):
    """Case 4: Button has disabled={!isFormValid} guard.

    Tests timed out clicking disabled button -> Tree-Sitter flags state guard!
    """
    code = """
    export function ApplicationForm({ isFormValid, onSubmit }) {
        return (
            <form>
                <button disabled={!isFormValid} onClick={onSubmit}>
                    Submit Application
                </button>
            </form>
        );
    }
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        fpath = Path(tmpdir) / "ApplicationForm.tsx"
        fpath.write_text(code, encoding="utf-8")
        info = inspector._inspect_file(Path(tmpdir), fpath)

    assert info is not None
    ux = info["ux_elements"]
    assert len(ux) == 1
    btn = ux[0]

    # Verify disabled guard expression extraction
    assert btn["tag"] == "button"
    assert btn["disabled_guard"] == "!isFormValid"
    assert btn["handler"] == "onClick={onSubmit}"

    analysis = analyze_ux_false_positive(ux, {"name": "Submit Application", "role": "button"})
    assert analysis is not None
    assert analysis["has_disabled_guard"] is True
    print("\n--- CASE 4 OUTPUT ---")
    print("Extracted Element:", btn)
    print("Disabled Guard Detected:", btn["disabled_guard"])


def test_case_5_modal_dialog_wrapping(inspector):
    """Case 5: Button moved inside a <Dialog><DialogContent> wrapper."""
    code = """
    export function DeleteAccountModal({ isOpen, onConfirm }) {
        return (
            <Dialog open={isOpen}>
                <DialogContent>
                    <p>Are you sure?</p>
                    <Button onClick={onConfirm} data-testid="confirm-delete">
                        Delete Account
                    </Button>
                </DialogContent>
            </Dialog>
        );
    }
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        fpath = Path(tmpdir) / "DeleteAccountModal.tsx"
        fpath.write_text(code, encoding="utf-8")
        info = inspector._inspect_file(Path(tmpdir), fpath)

    assert info is not None
    ux = info["ux_elements"]
    assert len(ux) == 1
    btn = ux[0]

    # Verify parent container hierarchy
    assert btn["tag"] == "Button"
    assert "Dialog" in btn["parent_wrappers"]
    assert "DialogContent" in btn["parent_wrappers"]
    assert btn["test_id"] == "confirm-delete"
    print("\n--- CASE 5 OUTPUT ---")
    print("Extracted Element:", btn)
    print("Parent Containers Tracked:", btn["parent_wrappers"])


def test_format_llm_ux_context_prompt_generation(inspector):
    """Test generating the LLM context prompt block from indexed UX elements."""
    inspection_result = {
        "ux_elements": [
            {
                "tag": "Button",
                "text": "Complete Purchase",
                "test_id": "checkout-cta",
                "aria_label": None,
                "handler": "onClick={handlePayment}",
                "disabled_guard": "!isCartValid",
                "parent_wrappers": ["Dialog", "DialogContent"],
                "file": "src/components/CheckoutModal.tsx",
                "line": 42,
            }
        ]
    }

    prompt_block = format_llm_ux_context(inspection_result, {"name": "Place Order"})
    assert "Repository UX Semantic Context" in prompt_block
    assert "Complete Purchase" in prompt_block
    assert "handlePayment" in prompt_block
    assert "checkout-cta" in prompt_block
    assert "disabled={!isCartValid}" in prompt_block
    assert "DialogContent" in prompt_block

    print("\n--- FORMATTED LLM PROMPT BLOCK ---")
    print(prompt_block)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main(["-v", "-s", __file__]))
