# Backend Views & API Endpoints

## Overview

All API views defined in `backend/app/views.py`. Uses DRF ViewSets with custom actions.

---

## ViewSet Summary

| ViewSet | Base Endpoint | Auth | Purpose |
|---------|---------------|------|---------|
| `AudioUploadViewSet` | `/clips/` | ✓ | Upload audio, trigger processing |
| `FastFeedViewSet` | `/feed/` | ✓ | Redis-backed personalized feed |
| `ClipInteractionViewSet` | `/interactions/{id}/` | ✓ | Like, skip, telemetry |
| `ShareViewSet` | `/share/` | ✓ | Peer-to-peer sharing, inbox |
| `CommentViewSet` | `/comments/` | ✓ | Nested threaded comments |
| `FollowViewSet` | `/follow/{id}/` | ✓ | Follow/unfollow users |
| `TagsViewSet` | `/tags/` | ✓ | Cold-start vector bootstrapping |
| `SuggestionViewSet` | `/suggestions/` | ✓ | Category-scoped recommendations |
| `ProfileViewSet` | `/profile/` | ✓ | User profiles |
| `RegisterView` | `/auth/register/` | Public | User registration |

---

## 1. AudioUploadViewSet (`/clips/`)

```python
class AudioUploadViewSet(viewsets.ModelViewSet):
    queryset = AudioClip.objects.all()
    serializer_class = AudioUploadSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]

    def get_queryset(self):
        return AudioClip.objects.filter(creator=self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        clip = serializer.save()
        transaction.on_commit(lambda: process_audio_to_hls.delay(clip.id))
        return Response({
            "message": "Audio uploading and processing in background.",
            "clip_id": clip.id,
            "status": clip.status
        }, status=status.HTTP_202_ACCEPTED)
```

**Flow:**
1. Validate file (type, size) via `AudioUploadSerializer`
2. Create `AudioClip(status='processing')` with creator=request.user
3. `transaction.on_commit` → enqueue `process_audio_to_hls` **after DB commit**
4. Return 202 with clip_id

**Why `transaction.on_commit`?** Guarantees clip row exists before worker picks up task. If transaction rolls back, task never enqueued.

**Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| POST | `/clips/` | Upload new clip |
| GET | `/clips/` | List user's clips |
| GET | `/clips/{id}/` | Retrieve clip |
| PATCH/PUT | `/clips/{id}/` | Update metadata |
| DELETE | `/clips/{id}/` | Delete clip |

---

## 2. FastFeedViewSet (`/feed/`)

```python
class FastFeedViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        user_id = request.user.id
        redis_key = f"user_feed:{user_id}"
        redis_client = cache.client.get_client()

        clip_ids_bytes = redis_client.lpop(redis_key, 10)
        
        if not clip_ids_bytes:
            refill_user_feed.delay(user_id, count=40)
            clip_ids_bytes = redis_client.lpop(redis_key, 10)
            if not clip_ids_bytes:
                return Response({"results": [], "message": "You've caught up!"})

        clip_ids = [vid.decode('utf-8') for vid in clip_ids_bytes]
        queue_length = redis_client.llen(redis_key)

        preserved_order = Case(*[When(pk=pk, then=pos) for pos, pk in enumerate(clip_ids)])
        clips = AudioClip.objects.filter(id__in=clip_ids).order_by(preserved_order)

        serializer = FeedClipSerializer(clips, many=True, context={'request': request})
        
        return Response({
            "next": "auto_trigger",
            "queue_health": queue_length,
            "results": serializer.data
        })
```

**Flow:**
1. LPOP 10 clip IDs from `user_feed:{user_id}`
2. If empty: trigger `refill_user_feed.delay(user_id, 40)`, then LPOP again
3. If still empty: return "caught up"
4. Fetch clips preserving Redis order (Case/When)
5. Serialize with `FeedClipSerializer` (generates signed HLS URLs)

**Queue health:** Returns remaining queue length for frontend preloading.

**Refill trigger:** Single refill call (previous code had duplicate `.delay()` — fixed).

---

## 3. ClipInteractionViewSet (`/interactions/{id}/`)

### toggle_like
```python
@action(detail=True, methods=['post'], url_path='toggle-like')
def toggle_like(self, request, pk=None):
    clip = self.get_object()
    user = request.user

    interaction, created = UserInteraction.objects.get_or_create(
        user=user, clip=clip, interaction_type='like',
        defaults={'is_active': True}
    )

    if not created:
        interaction.is_active = not interaction.is_active
        interaction.save()

    return Response({'status': 'liked' if interaction.is_active else 'unliked'})
```

**Behavior:** Toggles `is_active` (preserves history for time-decay). `UserInteraction.save()` handles counter increment/decrement via `F()`.

### register_skip (legacy)
```python
@action(detail=True, methods=['post'], url_path='register-skip')
def register_skip(self, request, pk=None):
    # Expects: listen_duration_ms, reel_position_ms, reel_id
    # Calculates completion_rate = min(listen_duration / reel_position, 1.0)
    # Creates/updates UserInteraction(type='view')
```
**Note:** Uses client-provided `reel_position_ms` (inaccurate). Prefer `log_telemetry`.

### log_telemetry (preferred)
```python
@action(detail=True, methods=['post'], url_path='log-telemetry')
def log_telemetry(self, request, pk=None):
    clip = self.get_object()
    # Expects: action_type (view/like/share/skip), watch_time_ms
    # Calculates completion_rate = min(watch_time_ms / clip.duration_ms, 1.0) SECURELY
    # Creates/updates UserInteraction with telemetry
```

**Security:** Uses server-side `clip.duration_ms` — prevents client manipulation of completion rate.

---

## 4. ShareViewSet (`/share/`)

```python
class ShareViewSet(viewsets.ModelViewSet):
    serializer_class = ShareEventSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return ShareEvent.objects.filter(receiver=self.request.user)

    @action(detail=False, methods=['get'], url_path='find-user')
    def find_user(self, request):
        # Finds user by username for sharing

    @action(detail=True, methods=['post'], url_path='send-share')
    def send_share(self, request, pk=None):
        clip = get_object_or_404(AudioClip, pk=pk)
        receiver = get_object_or_404(User, id=request.data['receiver_id'])
        
        # 1. Log interaction (increments clip.shares)
        UserInteraction.objects.get_or_create(user=request.user, clip=clip, interaction_type='share')
        
        # 2. Create ShareEvent (inbox entry)
        ShareEvent.objects.create(sender=request.user, receiver=receiver, clip=clip)
        
        return Response({'status': 'shared successfully'})

    @action(detail=True, methods=['delete'], url_path='share-delete')
    def share_delete(self, request, pk=None):
        ShareEvent.objects.filter(pk=pk, receiver=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'], url_path='mark-read')
    def mark_read(self, request, pk=None):
        ShareEvent.objects.filter(pk=pk, receiver=request.user).update(is_read=True)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['get'], url_path='inbox')
    def inbox(self, request):
        shares = ShareEvent.objects.filter(receiver=request.user).select_related('sender', 'clip').order_by('-created_at')
        return Response(ShareEventSerializer(shares, many=True).data)

    @action(detail=False, methods=['get'], url_path='unread-count')
    def unread_count(self, request):
        count = ShareEvent.objects.filter(receiver=request.user, is_read=False).count()
        return Response({'unread': count})
```

**Key behaviors:**
- `send_share`: Creates BOTH `UserInteraction(type='share')` AND `ShareEvent`
- `share_delete`: Removes inbox entry only, **does not decrement** clip.shares
- Inbox ordered by `-created_at` with `select_related` for sender/clip

---

## 5. CommentViewSet (`/comments/`)

```python
class CommentViewSet(viewsets.ModelViewSet):
    serializer_class = CommentSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = CommentCursorPagination
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['clip', 'parent']

    def get_queryset(self):
        return Comment.objects.select_related('author').all()
```

**Filtering:**
- `GET /comments/?clip={uuid}` — all comments for clip
- `GET /comments/?parent={uuid}` — replies to comment

**Pagination:** Cursor-based (20 per page).

**Counter logic:** In `Comment.save()/delete()` (model), not view.

---

## 6. FollowViewSet (`/follow/{id}/`)

```python
class FollowViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=True, methods=['post'], url_path='toggle-follow')
    def toggle_follow(self, request, pk=None):
        target_user = get_object_or_404(User, pk=pk)
        current_user = request.user

        if target_user == current_user:
            return Response({'error': 'You cannot follow yourself.'}, status=400)

        if current_user.following.filter(pk=target_user.pk).exists():
            current_user.following.remove(target_user)
            return Response({'status': 'unfollowed'})
        else:
            current_user.following.add(target_user)
            return Response({'status': 'followed'}, status=201)
```

**Not currently used in recommendation engine** — social graph stored but not factored into feed (except follow wedge in refill).

---

## 7. TagsViewSet (`/tags/initialize/`)

```python
class TagsViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=['post'], url_path='initialize')
    def initialize_vectors(self, request):
        user = request.user
        selected_tags = request.data.get('selected_tags', [])
        
        baseline_clips = AudioClip.objects.filter(
            tags__overlap=selected_tags,
            semantic_vector__isnull=False,
            acoustic_vector__isnull=False
        ).order_by('-likes')[:100]
        
        if not baseline_clips:
            return Response({"error": "Not enough data to build baseline."}, status=400)
            
        sem_vectors = [np.array(clip.semantic_vector) for clip in baseline_clips]
        ac_vectors = [np.array(clip.acoustic_vector) for clip in baseline_clips]
        
        user.long_term_semantic = (np.mean(sem_vectors, axis=0)).tolist()
        user.long_term_acoustic = (np.mean(ac_vectors, axis=0)).tolist()
        user.save()
        
        refill_user_feed.delay(user.id, count=30)
        return Response({"status": "Algorithm initialized. Feed is ready."})
```

**Cold-start flow:**
1. User selects tags on onboarding
2. Finds top 100 liked clips matching tags
3. Averages their semantic/acoustic vectors
4. Stores as user's long-term baseline
5. Triggers immediate feed refill

---

## 8. SuggestionViewSet (`/suggestions/`)

```python
class SuggestionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = FeedClipSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = FeedCursorPagination

    def get_queryset(self):
        user = self.request.user
        category = self.request.query_params.get('category')
        
        queryset = AudioClip.objects.filter(status='ready', category=category)
        
        sem_query, ac_query = calculate_time_decayed_vectors(user)
        
        if sem_query and ac_query:
            queryset = queryset.annotate(
                combined_distance=(
                    CosineDistance('semantic_vector', sem_query) + 
                    CosineDistance('acoustic_vector', ac_query)
                )
            ).order_by('combined_distance')
            
        user_like_subquery = UserInteraction.objects.filter(
            clip=OuterRef('pk'), user=user, interaction_type='like'
        )
        return queryset.annotate(user_has_liked=Exists(user_like_subquery))
```

**Difference from main feed:**
- Category-scoped (filtered by `category` param)
- No engagement_velocity in ranking
- No explore wedge (80/20 mix)
- Pure vector similarity within category

---

## 9. ProfileViewSet (`/profile/`)

```python
class ProfileViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def _annotate_user(self, user):
        return User.objects.annotate(
            followers_count=Count('followers', distinct=True),
            following_count=Count('following', distinct=True),
            uploads_count=Count('audio_clips', distinct=True)
        ).get(pk=user.pk)

    @action(detail=False, methods=['get'], url_path='me')
    def me(self, request):
        user = self._annotate_user(request.user)
        return Response(OwnProfileSerializer(user, context={'request': request}).data)

    @action(detail=False, methods=['patch'], url_path='me/update', parser_classes=[...])
    def update_me(self, request):
        serializer = ProfileUpdateSerializer(request.user, data=request.data, partial=True, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def retrieve(self, request, pk=None):
        target = get_object_or_404(
            User.objects.annotate(...), pk=pk
        )
        return Response(PublicProfileSerializer(target, context={'request': request}).data)

    @action(detail=True, methods=['get'], url_path='clips')
    def user_clips(self, request, pk=None):
        target = get_object_or_404(User, pk=pk)
        clips = AudioClip.objects.filter(creator=target, status='ready').order_by('-created_at')
        # paginated with FeedCursorPagination
```

**Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| GET | `/profile/me/` | Own profile (with liked_clips) |
| PATCH | `/profile/me/update/` | Update username/avatar |
| GET | `/profile/{id}/` | Public profile |
| GET | `/profile/{id}/clips/` | User's ready clips (paginated) |

---

## 10. RegisterView (`/auth/register/`)

```python
class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    permission_classes = (AllowAny,)
    serializer_class = RegisterSerializer
```
Public registration — returns tokens via `dj-rest-auth` integration.

---

## Pagination Classes

### FeedCursorPagination
```python
class FeedCursorPagination(CursorPagination):
    page_size = 10
    ordering = '-created_at'
```

### CommentCursorPagination
```python
class CommentCursorPagination(CursorPagination):
    page_size = 20
    ordering = '-created_at'
```

---

## N+1 Query Prevention

All list views use `Exists` subquery for `is_liked`:
```python
user_like_subquery = UserInteraction.objects.filter(
    clip=OuterRef('pk'), user=user, interaction_type='like', is_active=True
)
queryset.annotate(user_has_liked=Exists(user_like_subquery))
```

Single query fetches clips + like status — no per-clip query.

---

## Permissions

- All ViewSets: `permission_classes = [permissions.IsAuthenticated]`
- `RegisterView`: `AllowAny`
- Object-level: `get_queryset()` filters by request.user where appropriate

---

*Source: `backend/app/views.py`, `backend/app/serializers.py`, `backend/app/tasks.py` (for calculate_time_decayed_vectors)*