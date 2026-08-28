"""Standard MCP Registry discovery adapter.

The adapter consumes the public MCP Registry API shape rather than a vendor
marketplace.  Compatible registry deployments can replace the default endpoint
through ``MOUNIR_MCP_REGISTRY_URL`` without changing application behavior.
"""

from __future__ import annotations

import os
import shlex
from urllib.parse import quote

import httpx


DEFAULT_PROVIDER = "mcp-registry"
REGISTRY_URL = os.environ.get(
    "MOUNIR_MCP_REGISTRY_URL", "https://registry.modelcontextprotocol.io"
).rstrip("/")
REGISTRY_NAME = os.environ.get(
    "MOUNIR_MCP_REGISTRY_NAME", "Official MCP Registry"
).strip()
TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def providers() -> list[dict]:
    return [
        {
            "id": DEFAULT_PROVIDER,
            "name": REGISTRY_NAME,
            "url": REGISTRY_URL,
        }
    ]


def _error(response: httpx.Response) -> RuntimeError:
    try:
        payload = response.json()
        detail = (
            payload.get("message")
            or payload.get("detail")
            or payload.get("title")
            or payload.get("error")
            or response.text
        )
    except (ValueError, AttributeError):
        detail = response.text
    detail = str(detail or response.reason_phrase).strip()
    return RuntimeError(f"The MCP Registry request failed: {detail[:300]}")


def _identity(value: str) -> str:
    identity = str(value or "").strip()
    if not identity:
        raise ValueError("Choose an MCP server from the Registry.")
    if any(character in identity for character in ("\x00", "\r", "\n")):
        raise ValueError("The MCP Registry reference is invalid.")
    return identity


def _fixed_arguments(values) -> tuple[list[str], list[str]]:
    arguments: list[str] = []
    required: list[str] = []
    for raw in values or []:
        if not isinstance(raw, dict):
            continue
        value = raw.get("value")
        name = str(raw.get("name") or "").strip()
        if value not in (None, ""):
            if raw.get("type") == "named" and name:
                arguments.append(name)
            arguments.append(str(value))
        elif raw.get("isRequired"):
            required.append(name or str(raw.get("valueHint") or "argument"))
    return arguments, required


def _variables(values) -> tuple[dict[str, str], list[str]]:
    configured: dict[str, str] = {}
    required: list[str] = []
    for raw in values or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        default = raw.get("default")
        if default not in (None, ""):
            configured[name] = str(default)
        elif raw.get("isRequired"):
            configured[name] = ""
        if raw.get("isRequired"):
            required.append(name)
    return configured, required


def _package_command(package: dict) -> tuple[str, list[str]]:
    registry_type = str(package.get("registryType") or "").strip().lower()
    identifier = str(package.get("identifier") or "").strip()
    version = str(package.get("version") or "").strip()
    runtime = str(package.get("runtimeHint") or "").strip()
    if not runtime:
        runtime = {"npm": "npx", "pypi": "uvx", "oci": "docker"}.get(
            registry_type, ""
        )
    if not runtime or not identifier:
        return "", ["installation command"]

    runtime_arguments, runtime_required = _fixed_arguments(
        package.get("runtimeArguments")
    )
    package_arguments, package_required = _fixed_arguments(
        package.get("packageArguments")
    )
    requirements = [*runtime_required, *package_required]
    lowered_runtime = runtime.casefold()

    if lowered_runtime == "docker":
        if not runtime_arguments:
            runtime_arguments = ["run", "--rm", "-i"]
        variables, _ = _variables(package.get("environmentVariables"))
        for name in variables:
            runtime_arguments.extend(["-e", name])
        if identifier not in runtime_arguments:
            runtime_arguments.append(identifier)
    else:
        rendered_identifier = identifier
        if version:
            if registry_type == "npm" and "@" not in identifier[1:]:
                rendered_identifier = f"{identifier}@{version}"
            elif registry_type == "pypi" and "==" not in identifier:
                rendered_identifier = f"{identifier}=={version}"
        if not any(
            argument == identifier
            or argument == rendered_identifier
            or argument.startswith(f"{identifier}@")
            for argument in runtime_arguments
        ):
            if lowered_runtime == "npx" and "-y" not in runtime_arguments:
                runtime_arguments.insert(0, "-y")
            runtime_arguments.append(rendered_identifier)
        runtime_arguments.extend(package_arguments)
    return shlex.join([runtime, *runtime_arguments]), requirements


def _install_options(server: dict) -> list[dict]:
    options: list[dict] = []
    for index, remote in enumerate(server.get("remotes") or []):
        if not isinstance(remote, dict):
            continue
        remote_type = str(remote.get("type") or "").strip().lower()
        transport = {
            "streamable-http": "streamable_http",
            "streamable_http": "streamable_http",
            "sse": "sse",
        }.get(remote_type)
        url = str(remote.get("url") or "").strip()
        if (
            not transport
            or not url.startswith(("http://", "https://"))
            or "{" in url
            or "}" in url
        ):
            continue
        headers, required = _variables(remote.get("headers"))
        options.append(
            {
                "id": f"remote:{index}",
                "kind": "remote",
                "label": "Remote HTTP" if transport == "streamable_http" else "Legacy SSE",
                "transport": transport,
                "connection": url,
                "headers": headers,
                "env": {},
                "auth_scheme": "custom" if headers else "",
                "requirements": required,
            }
        )

    for index, package in enumerate(server.get("packages") or []):
        if not isinstance(package, dict):
            continue
        package_transport = package.get("transport")
        package_transport = (
            package_transport if isinstance(package_transport, dict) else {}
        )
        if str(package_transport.get("type") or "stdio").strip().lower() != "stdio":
            continue
        command, argument_requirements = _package_command(package)
        if not command:
            continue
        environment, environment_requirements = _variables(
            package.get("environmentVariables")
        )
        registry_type = str(package.get("registryType") or "Package").strip()
        options.append(
            {
                "id": f"package:{index}",
                "kind": "package",
                "label": f"{registry_type.upper()} package",
                "transport": "stdio",
                "connection": command,
                "headers": {},
                "env": environment,
                "auth_scheme": "",
                "requirements": [
                    *environment_requirements,
                    *argument_requirements,
                ],
            }
        )
    return options


def _requirement_labels(values) -> list[str]:
    if isinstance(values, dict):
        entries = [
            {"name": name, **(value if isinstance(value, dict) else {})}
            for name, value in values.items()
        ]
    else:
        entries = values or []
    labels: list[str] = []
    for raw in entries:
        if not isinstance(raw, dict) or not raw.get("isRequired"):
            continue
        name = str(
            raw.get("name") or raw.get("valueHint") or raw.get("placeholder") or "Value"
        ).strip()
        description = str(raw.get("description") or "").strip()
        labels.append(f"{name} — {description}" if description else name)
    return labels


def _published_options(server: dict, install_options: list[dict]) -> list[dict]:
    configurable = {option["id"] for option in install_options}
    published: list[dict] = []
    for index, remote in enumerate(server.get("remotes") or []):
        if not isinstance(remote, dict):
            continue
        remote_type = str(remote.get("type") or "").strip()
        option_id = f"remote:{index}"
        published.append(
            {
                "id": option_id,
                "kind": "remote",
                "label": (
                    "Remote HTTP"
                    if remote_type in {"streamable-http", "streamable_http"}
                    else "Legacy SSE"
                    if remote_type == "sse"
                    else "Remote connection"
                ),
                "transport": remote_type or "Not specified",
                "address": str(remote.get("url") or ""),
                "registry": "",
                "version": "",
                "runtime": "",
                "requirements": [
                    *_requirement_labels(remote.get("headers")),
                    *_requirement_labels(remote.get("variables")),
                ],
                "integrity_available": False,
                "configurable": option_id in configurable,
            }
        )
    for index, package in enumerate(server.get("packages") or []):
        if not isinstance(package, dict):
            continue
        option_id = f"package:{index}"
        package_transport = package.get("transport")
        package_transport = (
            package_transport if isinstance(package_transport, dict) else {}
        )
        published.append(
            {
                "id": option_id,
                "kind": "package",
                "label": f"{str(package.get('registryType') or 'Package').upper()} package",
                "transport": str(package_transport.get("type") or "stdio"),
                "address": str(package.get("identifier") or ""),
                "registry": str(package.get("registryBaseUrl") or ""),
                "version": str(package.get("version") or ""),
                "runtime": str(package.get("runtimeHint") or ""),
                "requirements": [
                    *_requirement_labels(package.get("environmentVariables")),
                    *_requirement_labels(package.get("runtimeArguments")),
                    *_requirement_labels(package.get("packageArguments")),
                ],
                "integrity_available": bool(package.get("fileSha256")),
                "configurable": option_id in configurable,
            }
        )
    return published


def _item(raw: dict) -> dict:
    server = raw.get("server") if isinstance(raw.get("server"), dict) else raw
    identity = _identity(server.get("name"))
    metadata = raw.get("_meta") if isinstance(raw.get("_meta"), dict) else {}
    official = metadata.get("io.modelcontextprotocol.registry/official")
    official = official if isinstance(official, dict) else {}
    server_metadata = server.get("_meta")
    server_metadata = server_metadata if isinstance(server_metadata, dict) else {}
    publisher = server_metadata.get(
        "io.modelcontextprotocol.registry/publisher-provided"
    )
    publisher = publisher if isinstance(publisher, dict) else {}
    repository = server.get("repository")
    repository = repository if isinstance(repository, dict) else {}
    install_options = _install_options(server)
    title = str(server.get("title") or "").strip()
    if not title:
        title = identity.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").title()
    return {
        "provider": DEFAULT_PROVIDER,
        "provider_name": REGISTRY_NAME,
        "reference": identity,
        "name": title,
        "description": str(server.get("description") or ""),
        "version": str(server.get("version") or ""),
        "repository_url": str(repository.get("url") or ""),
        "repository_source": str(repository.get("source") or ""),
        "repository_subfolder": str(repository.get("subfolder") or ""),
        "website_url": str(server.get("websiteUrl") or ""),
        "status": str(official.get("status") or ""),
        "status_message": str(official.get("statusMessage") or ""),
        "status_changed_at": official.get("statusChangedAt"),
        "published_at": official.get("publishedAt"),
        "updated_at": official.get("updatedAt"),
        "is_latest": (
            official.get("isLatest")
            if isinstance(official.get("isLatest"), bool)
            else None
        ),
        "publisher_contact": str(publisher.get("contactEmail") or ""),
        "install_options": install_options,
        "published_options": _published_options(server, install_options),
    }


async def browse(query: str = "", cursor: str = "", limit: int = 24) -> dict:
    params: dict[str, str | int] = {
        "limit": max(1, min(int(limit), 50)),
        "version": "latest",
    }
    if query.strip():
        params["search"] = query.strip()
    if cursor.strip():
        params["cursor"] = cursor.strip()
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        response = await client.get(
            f"{REGISTRY_URL}/v0.1/servers",
            params=params,
            headers={"Accept": "application/json", "User-Agent": "Mounir-Agent-Studio/1"},
        )
    if not response.is_success:
        raise _error(response)
    payload = response.json()
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return {
        "provider": DEFAULT_PROVIDER,
        "provider_name": REGISTRY_NAME,
        "items": [_item(raw) for raw in payload.get("servers") or []],
        "next_cursor": str(metadata.get("nextCursor") or ""),
    }


async def details(reference: str, version: str = "latest") -> dict:
    identity = _identity(reference)
    selected_version = str(version or "latest").strip() or "latest"
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        response = await client.get(
            f"{REGISTRY_URL}/v0.1/servers/{quote(identity, safe='')}/versions/"
            f"{quote(selected_version, safe='')}",
            headers={"Accept": "application/json", "User-Agent": "Mounir-Agent-Studio/1"},
        )
    if not response.is_success:
        raise _error(response)
    return _item(response.json())
