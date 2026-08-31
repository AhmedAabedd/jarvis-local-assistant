"""Compatibility wrapper for the LangGraph-backed Mounir agent."""

from __future__ import annotations

from .langgraph_agent import Agent, AssistantCompletion, build_graph

__all__ = ["Agent", "AssistantCompletion", "build_graph"]
