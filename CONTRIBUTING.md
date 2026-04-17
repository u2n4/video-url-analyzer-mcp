# Contributing to Video URL Analyzer MCP

Thank you for your interest in contributing! This document provides guidelines and instructions for contributing to this project.

## How to Report Bugs

1. **Search existing issues** — Check [GitHub Issues](https://github.com/u2n4/video-url-analyzer-mcp/issues) to see if the bug has already been reported.
2. **Create a new issue** — If the bug hasn't been reported, open a new issue with:
   - A clear, descriptive title
   - Steps to reproduce the bug
   - Expected behavior vs. actual behavior
   - Your environment (OS, Python version, relevant package versions)
   - Any error messages or logs (redact API keys!)

## How to Request Features

1. **Search existing issues** — Someone may have already suggested it.
2. **Open a new issue** with the `enhancement` label and describe:
   - The problem you're trying to solve
   - Your proposed solution
   - Any alternatives you've considered

## Development Setup

1. **Fork the repository** on GitHub.

2. **Clone your fork:**
   ```bash
   git clone https://github.com/YOUR_USERNAME/video-url-analyzer-mcp.git
   cd video-url-analyzer-mcp
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Create a `.env` file:**
   ```
   GEMINI_API_KEY=your_gemini_api_key_here
   ```

5. **Run the server:**
   ```bash
   python server.py
   ```

## Pull Request Guidelines

1. **Fork** the repository and create a new branch from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes** — Keep commits focused and atomic.

3. **Write clear commit messages** following conventional commits:
   ```
   feat: add support for Vimeo URLs
   fix: handle timeout on large video downloads
   docs: update troubleshooting section
   ```

4. **Test your changes** — Ensure the server starts correctly and your changes work as expected.

5. **Push to your fork** and submit a Pull Request:
   ```bash
   git push origin feature/your-feature-name
   ```

6. **In your PR description**, include:
   - What the change does
   - Why the change is needed
   - How you tested it

## Code Style

- Follow existing code patterns in `server.py`
- Use type hints for function parameters and return types
- Add docstrings to new functions
- Handle errors explicitly with clear error messages
- Clean up temporary files in `finally` blocks

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](https://www.contributor-covenant.org/version/2/1/code_of_conduct/). By participating, you are expected to uphold this code. Please report unacceptable behavior by opening an issue.

## Questions?

If you have questions about contributing, feel free to open an issue with the `question` label.
