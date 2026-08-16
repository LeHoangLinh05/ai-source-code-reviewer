export function isSupportedRepositoryUrl(value: string) {
  try {
    const repositoryUrl = new URL(value);
    const hostname = repositoryUrl.hostname.toLowerCase();
    const decodedPath = decodeURIComponent(repositoryUrl.pathname);
    const pathSegments = repositoryUrl.pathname.split("/").filter(Boolean);
    const repositoryName = pathSegments.at(-1)?.replace(/\.git$/, "");

    return (
      repositoryUrl.protocol === "https:" &&
      repositoryUrl.port === "" &&
      repositoryUrl.username === "" &&
      repositoryUrl.password === "" &&
      repositoryUrl.search === "" &&
      repositoryUrl.hash === "" &&
      hostname === "github.com" &&
      decodedPath === repositoryUrl.pathname &&
      pathSegments.length >= 2 &&
      repositoryName !== undefined &&
      repositoryName !== ""
    );
  } catch {
    return false;
  }
}
