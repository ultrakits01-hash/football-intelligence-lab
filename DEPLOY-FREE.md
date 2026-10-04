# FIL — £0 public deployment

## Backend/app: Render Free

This package is ready for Render's free Python web-service tier.

- Build: `pip install -r requirements.txt`
- Start: `uvicorn apps.api.app.main:app --host 0.0.0.0 --port $PORT`
- Health check: `/health`
- Blueprint: `render.yaml`

### Important data rule

The release ZIP intentionally does not contain your local installed datasets. Before publishing, copy only datasets/assets you have permission to redistribute into this project. Never commit `.env`, API tokens, credentials, or private/licence-restricted data.

### GitHub → Render

1. Create a GitHub repository (private is fine).
2. Commit this project plus only redistributable `data/` files.
3. In Render choose **New → Blueprint** (or Web Service), connect the repository, and use `render.yaml`.
4. Select the Free service if Render asks for a plan.
5. After deploy, verify `https://YOUR-SERVICE.onrender.com/health`.
6. Put the resulting public app URL into the FIL marketing site's Open FIL button.

### Free-tier behaviour

Render Free sleeps after inactivity, so the first request after a quiet period can take roughly a minute. The filesystem is ephemeral: runtime-written cache/data can disappear after restart/redeploy. Treat bundled datasets as read-only and do not rely on runtime filesystem writes for permanent state.

## Marketing site: Cloudflare Pages Free

Deploy the separate FIL public static site to Cloudflare Pages. Static asset requests are free/unlimited on Pages; the free plan has a build quota. Use the Render URL for the Open FIL CTA.
