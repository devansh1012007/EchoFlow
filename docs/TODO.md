- add logs
- add a function to verify that the clip is under certain storage limit
- add a frontend 
- setup scalper 


- add comments to the left 
- add follow unfollow functionality
- after clicking on the reel sent , the notifaction should go from the inbox but the reel should stay there
- remove the audion bar

- chage the HF token , I have deleted it form my key from website.
- add all the keys as enviroment veriables 
- add rate limiting on every endpoint
- add input validation on audio upload file size/type
- the token refresh endpoint and register endpoint are not CSRF-protected if accessed from a browser context. -- the token refresh endpoint and register endpoint are not CSRF-protected if accessed from a browser context.
- ADD HTML sanitization or XSS protection for text = models.CharField(max_length=500)
- fix -- Share endpoint allows sharing to any user ID 
- add content moderation pipeline
- No database constraints on counter fields (likes, shares, skips, comment_count) -- Add validators=[MinValueValidator(0)] and database CHECK constraints.
- UserInteraction save() has a race condition -- Need to look into it (solve carefully)
- add soft delete for required models 
- ShareEvent uses BigAutoField primary key while other models use UUID --- ez fix but look into all the shit that will be iffected by that change and fix accordingly


- continue after solving these to 4th section 
- setup Continuous Integration (CI/CD)
- setup Lode balancing
- setup DB on aws and shit
- add dash board and improve audio uploading experience 
- Worker Auto-scaling


Personal TODO : 
- fix errors 
- setup Continuous Integration (CI/CD)
- check on business side and shit 
- add features 
- work on recommandation engine
- Media to object storage — move HLS output and uploads from local disk to S3-compatible storage (django-storages + boto3 are already dependencies)
- CDN delivery — serve HLS segments through a CDN for global low-latency streaming
- Real-time notifications — push events for shares/inbox via websockets or a streaming broker
- Recommendation at scale — replace brute-force cosine scans with a candidate-generation + ANN tier as the catalog grows
- Event-driven message bus — migrate the Celery/Redis broker to a durable event stream for idempotent, retryable processing
- Rate limiting & throttling — add DRF throttling and distributed rate limits to the API layer
- Observability stack — structured logging, metrics, tracing, and error tracking for production confidence

- make audit and planning docs on following :
    - * Stateful media storage in a horizontally scaling environment.
    - * Database write-contention from high-velocity telemetry.
    - * Memory and CPU exhaustion from collocated ML inference and media transcoding.
    - * The transition from a relational architecture to an event-driven architecture.



FIX BEFORE PRODUCTION
- The unconditional static_serve route is a stopgap, not a production answer. It's single-threaded blocking I/O with no caching headers — every HLS segment request now parks a gunicorn thread for its full duration. Fine at hackathon scale. When you're ready to matter to real users, that's an nginx-in-front-of-gunicorn problem (or S3/R2 + CDN), not a Django-routes problem.
- Frontend Fix: The frontend auth interceptor should treat any error on /auth/token/refresh/ (both 401 and 500) as a hard auth failure: immediately clear localStorage / cookies, reset client auth state, and navigate to /login.
- docker-compose.yml always sets REDIS_URL via .env, so the fallback never triggers in your current setup. But it's the identical failure mode waiting to happen: if you ever run a worker outside this exact compose file (a one-off script, a different deploy target, a k8s pod where the env var name got typo'd) with REDIS_URL unset, it won't error —