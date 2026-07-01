"""Docker Manager — interacts with the Docker daemon via /var/run/docker.sock to manage containers."""
import httpx
from app.core.logging import get_logger

logger = get_logger(__name__)

class DockerManager:
    async def restart_container(self, container_name: str) -> bool:
        """Restart a docker container using the Unix Domain Socket /var/run/docker.sock."""
        try:
            # UDS transport in httpx
            transport = httpx.AsyncHTTPTransport(uds="/var/run/docker.sock")
            async with httpx.AsyncClient(transport=transport) as client:
                # Docker API endpoint to restart a container
                url = f"http://localhost/containers/{container_name}/restart"
                resp = await client.post(url, timeout=15.0)
                if resp.status_code in (200, 204):
                    logger.info("Successfully restarted container %s via Docker socket", container_name)
                    return True
                logger.warning("Failed to restart container %s: Status %d, Response: %s", 
                               container_name, resp.status_code, resp.text)
        except Exception as e:
            logger.error("Error communicating with Docker socket: %s", e)
        return False

    async def restart_hermes_agent(self) -> bool:
        """Helper to restart the Hermes Agent container."""
        return await self.restart_container("whatsapp_hermes")

docker_manager = DockerManager()
