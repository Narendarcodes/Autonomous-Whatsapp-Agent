"""Docker Manager — interacts with the Docker daemon via /var/run/docker.sock to manage containers."""
import httpx
from app.core.logging import get_logger

logger = get_logger(__name__)

HERMES_CONTAINER = "whatsapp_hermes"
_DOCKER_SOCK = "/var/run/docker.sock"


async def _docker_api(method: str, path: str, json_body: dict | None = None) -> httpx.Response:
    """Single request against the Docker HTTP API over the unix socket."""
    transport = httpx.AsyncHTTPTransport(uds=_DOCKER_SOCK)
    async with httpx.AsyncClient(transport=transport) as client:
        url = f"http://localhost{path}"
        resp = await client.request(method, url, json=json_body, timeout=30.0)
        return resp


class DockerManager:
    async def restart_container(self, container_name: str) -> bool:
        """Restart a docker container using the Unix Domain Socket /var/run/docker.sock."""
        try:
            resp = await _docker_api("POST", f"/containers/{container_name}/restart")
            if resp.status_code in (200, 204):
                logger.info("Successfully restarted container %s via Docker socket", container_name)
                return True
            logger.warning("Failed to restart container %s: Status %d, Response: %s", 
                           container_name, resp.status_code, resp.text)
        except Exception as e:
            logger.error("Error communicating with Docker socket: %s", e)
        return False

    async def exec_detached(self, container_name: str, command: str) -> bool:
        """Run `command` inside `container_name` fully detached (fire-and-forget).

        Creates an exec instance (`bash -c command`) and starts it with
        Detach=True so no stream handling is needed. The process keeps running
        inside the container after this call returns.
        """
        try:
            create = await _docker_api(
                "POST",
                f"/containers/{container_name}/exec",
                {
                    "Cmd": ["bash", "-c", command],
                    "AttachStdout": False,
                    "AttachStderr": False,
                    "AttachStdin": False,
                    "Tty": False,
                },
            )
            if create.status_code not in (200, 201):
                logger.warning("Exec create failed on %s: %d %s", container_name, create.status_code, create.text)
                return False
            exec_id = create.json().get("Id")
            if not exec_id:
                return False
            start = await _docker_api("POST", f"/exec/{exec_id}/start", {"Detach": True, "Tty": False})
            if start.status_code in (200, 204):
                logger.info("Detached exec started in %s (exec=%s)", container_name, exec_id[:12])
                return True
            logger.warning("Exec start failed on %s: %d %s", container_name, start.status_code, start.text)
        except Exception as e:
            logger.error("Detached exec error on %s: %s", container_name, e)
        return False

    async def restart_hermes_agent(self) -> bool:
        """Helper to restart the Hermes Agent container."""
        return await self.restart_container(HERMES_CONTAINER)

docker_manager = DockerManager()
