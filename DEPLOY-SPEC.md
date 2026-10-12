# BizRegSignal Railway deployment specification

## Objective

Complete this deployment as one browser task in the existing `tigerops-win` Railway project.

The GitHub repository is created and populated through the GitHub CLI first:

```text
tigerops-win/bizreg-signal
```

## Browser task

1. Open the existing `tigerops-win` project in Railway.
2. Create a new service from the GitHub repository `tigerops-win/bizreg-signal`.
3. Confirm Railway detects and builds the repository Dockerfile.
4. Add this service environment variable exactly:

   ```text
   BIZREGSIGNAL_RECEIVING_ADDRESS=0xAF7B70D8487EE6193701597E67f56A5902d23913
   ```

5. Deploy the service.
6. Generate a Railway public domain for the service.
7. Open the generated domain at `/health`.
8. Verify the response is HTTP 200 JSON with this shape:

   ```json
   {
     "ok": true,
     "sales_enabled": true,
     "registrations": 0
   }
   ```

   `registrations` may be any non-negative integer matching the deployed seed dataset. The required checks are:

   - `ok` is `true`.
   - `sales_enabled` is `true`.
   - `registrations` is present and numeric.

9. Record the generated public domain as the production BizRegSignal base URL.

## Railway budget constraint

Use the existing Railway trial allocation and inspect the estimated or current project usage before leaving the service running. Do not add unnecessary replicas, paid add-ons, or oversized resources. Keep one service instance at Railway's smallest practical resource allocation.

## Possible human-only GitHub step

Railway's GitHub App may not yet have access to the new `tigerops-win/bizreg-signal` repository. If the repository is absent from Railway's repository picker, the only expected human step is granting that repository to the Railway GitHub App in GitHub's installation settings. After the grant, return to Railway, refresh the repository list, and continue the same deployment task.

## Persistence warning

The service writes replay protection and sales records beneath `/app/data`. Container-local files may disappear during a redeploy. Before accepting material paid traffic, attach a Railway volume mounted at:

```text
/app/data
```

The deployed seed `registrations.json` must also be present on that volume if mounting it masks the image's original `/app/data` directory. Seed the volume before switching production traffic, then confirm `/health` still reports the expected registration count.
