# Mounir web application

This directory contains the React and TypeScript interface for both the chat
dashboard and Agent Studio.

## Commands

```bash
npm install
npm run dev          # development server with FastAPI proxying
npm run build        # type-check and create ../web-dist
npm run format       # format source files
npm run format:check # verify formatting without changing files
```

Run `python server.py` from the repository root while using the Vite development
server. The production build is served directly by FastAPI.

## Organization

- `src/api/` contains the typed backend contract and HTTP client.
- `src/app/` owns routing and page-level lazy loading.
- `src/components/ui/` contains reusable, domain-independent controls.
- `src/features/chat/` owns conversation streaming, voice, and confirmations.
- `src/features/overview/` owns the React Flow agent topology.
- `src/features/resources/` owns models, MCP servers, and subagents.
- `src/features/settings/` owns profile, voice, messaging, and Heartbeat screens.
- `src/features/studio/` contains the Agent Studio layout and navigation.
- `src/hooks/` contains shared server-state hooks.
- `src/styles/` contains the application design system and responsive rules.

Keep backend state in TanStack Query, short-lived form/UI state inside the owning
feature, and generic controls in `components/ui`. New Agent Studio capabilities
should normally be added as feature pages instead of expanding the layout.
