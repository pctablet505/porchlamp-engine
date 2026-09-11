"""Shared pytest configuration.

The package is installed editable into the venv (``pip install -e .``), so
``import cvi`` resolves from anywhere. This file is intentionally minimal to
avoid coupling the per-module test suites to shared fixtures.
"""
