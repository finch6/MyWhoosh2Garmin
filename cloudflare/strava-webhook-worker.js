// Cloudflare Worker: Strava webhook -> GitHub Actions "Run workflow"
// Setup instructions: see SETUP.md, step 9.
//
// Variables (Worker → Settings → Variables and Secrets):
//   WEBHOOK_SECRET     (secret)  long random string; used in the URL path and as Strava's verify_token
//   GITHUB_TOKEN       (secret)  fine-grained GitHub token for this repo ("Actions: Read and write")
//   GITHUB_REPO        (text)    e.g. "finch6/MyWhoosh2Garmin"
//   WORKFLOW_FILE      (text)    file name in .github/workflows/, i.e. "run.yml"
//   GITHUB_BRANCH      (text)    optional, defaults to "main"
//   STRAVA_ATHLETE_ID  (text)    optional, your Strava athlete ID; ignores events for anyone else
//   DELAY_SECONDS      (text)    optional, defaults to "30"; gives Strava time to finish processing

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // Only answer on the secret path, so random visitors get nothing
    if (url.pathname !== `/${env.WEBHOOK_SECRET}`) {
      return new Response("Not found", { status: 404 });
    }

    // 1. Strava's one-off handshake when the subscription is created
    if (request.method === "GET") {
      const mode = url.searchParams.get("hub.mode");
      const token = url.searchParams.get("hub.verify_token");
      const challenge = url.searchParams.get("hub.challenge");
      if (mode === "subscribe" && token === env.WEBHOOK_SECRET && challenge) {
        return Response.json({ "hub.challenge": challenge });
      }
      return new Response("Forbidden", { status: 403 });
    }

    // 2. Activity events
    if (request.method === "POST") {
      let event;
      try {
        event = await request.json();
      } catch {
        return new Response("Bad request", { status: 400 });
      }

      const isNewActivity =
        event.object_type === "activity" && event.aspect_type === "create";
      const isMe =
        !env.STRAVA_ATHLETE_ID || String(event.owner_id) === String(env.STRAVA_ATHLETE_ID);

      if (isNewActivity && isMe) {
        // Strava wants a reply within 2 seconds, so trigger GitHub in the background
        ctx.waitUntil(triggerWorkflow(env, event));
      } else {
        console.log(
          `Ignored event: ${event.object_type}/${event.aspect_type} ` +
            `owner_id=${event.owner_id} (expected ${env.STRAVA_ATHLETE_ID || "any"})`
        );
      }
      return new Response("OK");
    }

    return new Response("Method not allowed", { status: 405 });
  },
};

async function triggerWorkflow(env, event) {
  const response = await fetch(
    `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${env.WORKFLOW_FILE}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "strava-webhook-worker",
      },
      body: JSON.stringify({
        ref: env.GITHUB_BRANCH || "main",
        inputs: { delay_seconds: env.DELAY_SECONDS || "30" },
      }),
    }
  );

  if (response.ok) {
    console.log(`Triggered workflow for Strava activity ${event.object_id}`);
  } else {
    console.log(`GitHub dispatch failed: ${response.status} ${await response.text()}`);
  }
}
