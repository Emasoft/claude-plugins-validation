#!/usr/bin/env bash
# setup_git_hooks.sh - Install git hooks for plugin validation
#
# This script sets up pre-push hooks that automatically validate and lint the plugin before pushes.
#
# Usage:
#   ./scripts/setup_git_hooks.sh          # Install hooks (default: copy)
#   ./scripts/setup_git_hooks.sh --symlink # Install hooks as symlinks
#   ./scripts/setup_git_hooks.sh --remove  # Remove installed hooks

set -euo pipefail

# Colors for output
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get the script's directory and repository root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
GIT_HOOKS_SRC="$REPO_ROOT/git-hooks"
GIT_HOOKS_DEST="$REPO_ROOT/.git/hooks"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Git Hooks Setup for Plugin Validation${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Parse arguments
USE_SYMLINKS=false
REMOVE_HOOKS=false

for arg in "$@"; do
    case $arg in
        --symlink)
            USE_SYMLINKS=true
            ;;
        --remove)
            REMOVE_HOOKS=true
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --symlink  Install hooks as symlinks (useful for development)"
            echo "  --remove   Remove installed hooks"
            echo "  --help     Show this help message"
            echo ""
            echo "Default behavior: Copy hooks to .git/hooks/"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $arg${NC}"
            echo "Use --help for usage information."
            exit 1
            ;;
    esac
done

# Check if .git directory exists
if [ ! -d "$REPO_ROOT/.git" ]; then
    echo -e "${RED}ERROR: .git directory not found.${NC}"
    echo "This script must be run from within a git repository."
    echo ""
    echo "To initialize a git repository, run:"
    echo "  git init"
    exit 1
fi

# Check if source hooks exist
if [ ! -d "$GIT_HOOKS_SRC" ]; then
    echo -e "${RED}ERROR: git-hooks directory not found.${NC}"
    echo "Expected location: $GIT_HOOKS_SRC"
    exit 1
fi

# Create .git/hooks directory if it doesn't exist
if [ ! -d "$GIT_HOOKS_DEST" ]; then
    echo -e "${BLUE}Creating .git/hooks directory...${NC}"
    mkdir -p "$GIT_HOOKS_DEST"
fi

# List of hooks to install
HOOKS=("pre-push")

# Remove hooks if requested
if [ "$REMOVE_HOOKS" = true ]; then
    echo -e "${BLUE}Removing installed hooks...${NC}"
    for hook in "${HOOKS[@]}"; do
        HOOK_PATH="$GIT_HOOKS_DEST/$hook"
        if [ -e "$HOOK_PATH" ] || [ -L "$HOOK_PATH" ]; then
            rm -f "$HOOK_PATH"
            echo -e "  ${GREEN}Removed:${NC} $hook"
        else
            echo -e "  ${YELLOW}Not found:${NC} $hook"
        fi
    done
    echo ""
    echo -e "${GREEN}Hooks removed successfully.${NC}"
    exit 0
fi

# Install hooks
echo -e "${BLUE}Installing git hooks...${NC}"
echo ""

for hook in "${HOOKS[@]}"; do
    SRC_PATH="$GIT_HOOKS_SRC/$hook"
    DEST_PATH="$GIT_HOOKS_DEST/$hook"

    # Check if source hook exists
    if [ ! -f "$SRC_PATH" ]; then
        echo -e "  ${RED}ERROR:${NC} Source hook not found: $SRC_PATH"
        continue
    fi

    # Remove existing hook if present
    if [ -e "$DEST_PATH" ] || [ -L "$DEST_PATH" ]; then
        echo -e "  ${YELLOW}Replacing existing:${NC} $hook"
        rm -f "$DEST_PATH"
    fi

    # Install hook (symlink or copy)
    if [ "$USE_SYMLINKS" = true ]; then
        ln -s "$SRC_PATH" "$DEST_PATH"
        echo -e "  ${GREEN}Symlinked:${NC} $hook"
    else
        cp "$SRC_PATH" "$DEST_PATH"
        echo -e "  ${GREEN}Copied:${NC} $hook"
    fi

    # Ensure hook is executable
    chmod +x "$DEST_PATH"
done

echo ""
echo -e "${BLUE}----------------------------------------${NC}"
echo -e "${GREEN}Git hooks installed successfully!${NC}"
echo ""
echo -e "${BLUE}Installed hooks:${NC}"
echo "  - pre-push: Read-only linting + plugin validation (blocks ALL issues)"
echo ""
echo -e "${BLUE}To test the hooks:${NC}"
echo "  git push --dry-run origin HEAD"
echo ""
echo -e "${BLUE}To bypass hooks temporarily:${NC}"
echo "  git commit --no-verify -m 'message'"
echo "  git push --no-verify"
echo ""
if [ "$USE_SYMLINKS" = true ]; then
    echo -e "${YELLOW}Note: Hooks are symlinked. Changes to git-hooks/ are immediate.${NC}"
else
    echo -e "${YELLOW}Note: Hooks are copied. Re-run this script after changes to git-hooks/.${NC}"
fi
