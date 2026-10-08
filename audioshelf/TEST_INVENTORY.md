# AudioShelf test inventory

Reviewed: 2026-10-08T23:04:14+01:00 (Europe/London).

Reviewed 0.6.6 coverage of Spotify CDN album art, exact canonical MusicBrainz matching, unmatched/live fallback into Record Store, pause/resume, optional skip controls and account-scoped settings. Expanded browser checks cover the bar height, outside-collection navigation and mobile layout; full validation runs in CI.

<!-- inventory: {"reviewed_at":"2026-10-08T23:04:14+01:00","review_note":"Reviewed 0.6.6 coverage of Spotify CDN album art, exact canonical MusicBrainz matching, unmatched/live fallback into Record Store, pause/resume, optional skip controls and account-scoped settings. Expanded browser checks cover the bar height, outside-collection navigation and mobile layout; full validation runs in CI.","source_sha256":"a4387407f87a08d658ab862a8bf4438b933299e180b3f328ded432fbaa20a4ce"} -->

## Backend cases (293)

1. `audioshelf/tests/test_accounts.py::test_account_creation_and_management_require_admin_and_fresh_password`
2. `audioshelf/tests/test_accounts.py::test_background_playback_is_account_and_session_scoped`
3. `audioshelf/tests/test_accounts.py::test_backups_and_replaceable_cache_are_account_scoped`
4. `audioshelf/tests/test_accounts.py::test_concurrent_requests_do_not_share_a_current_account`
5. `audioshelf/tests/test_accounts.py::test_cover_overrides_and_http_cache_cannot_cross_accounts`
6. `audioshelf/tests/test_accounts.py::test_credentials_and_signed_sessions_cannot_cross_accounts`
7. `audioshelf/tests/test_accounts.py::test_disabled_account_reenable_preserves_library_but_revokes_sessions`
8. `audioshelf/tests/test_accounts.py::test_existing_collection_tokens_and_security_stay_with_owner`
9. `audioshelf/tests/test_accounts.py::test_ingress_can_switch_users_and_signout_does_not_return_to_owner`
10. `audioshelf/tests/test_accounts.py::test_invalid_account_inputs_do_not_create_accounts[-alice-password-long-enough]`
11. `audioshelf/tests/test_accounts.py::test_invalid_account_inputs_do_not_create_accounts[../alice-alice-password-long-enough]`
12. `audioshelf/tests/test_accounts.py::test_invalid_account_inputs_do_not_create_accounts[aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-alice-password-long-enough]`
13. `audioshelf/tests/test_accounts.py::test_invalid_account_inputs_do_not_create_accounts[new-short]`
14. `audioshelf/tests/test_accounts.py::test_invalid_account_inputs_do_not_create_accounts[with space-alice-password-long-enough]`
15. `audioshelf/tests/test_accounts.py::test_invalid_oauth_never_connects_an_account[denied]`
16. `audioshelf/tests/test_accounts.py::test_invalid_oauth_never_connects_an_account[disabled]`
17. `audioshelf/tests/test_accounts.py::test_invalid_oauth_never_connects_an_account[expired]`
18. `audioshelf/tests/test_accounts.py::test_invalid_oauth_never_connects_an_account[unknown]`
19. `audioshelf/tests/test_accounts.py::test_library_preferences_mappings_diagnostics_and_exports_are_isolated`
20. `audioshelf/tests/test_accounts.py::test_login_rate_limit_is_shared_across_usernames`
21. `audioshelf/tests/test_accounts.py::test_new_login_or_disabled_old_session_cannot_cancel_owner_playback`
22. `audioshelf/tests/test_accounts.py::test_old_tab_account_header_cannot_mutate_newly_selected_library`
23. `audioshelf/tests/test_accounts.py::test_only_trusted_ingress_can_return_to_owner_after_signout`
24. `audioshelf/tests/test_accounts.py::test_password_change_and_admin_reset_revoke_only_target_account_and_persist`
25. `audioshelf/tests/test_accounts.py::test_pending_background_playback_stops_when_account_access_changes[disable]`
26. `audioshelf/tests/test_accounts.py::test_pending_background_playback_stops_when_account_access_changes[logout]`
27. `audioshelf/tests/test_accounts.py::test_pending_background_playback_stops_when_account_access_changes[reset]`
28. `audioshelf/tests/test_accounts.py::test_pending_background_playback_stops_when_account_access_changes[switch]`
29. `audioshelf/tests/test_accounts.py::test_playback_authorization_does_not_wait_on_account_management_lock`
30. `audioshelf/tests/test_accounts.py::test_spotify_callback_routes_to_initiating_account_after_browser_switch`
31. `audioshelf/tests/test_accounts.py::test_spotify_devices_playback_and_disconnect_use_only_current_account`
32. `audioshelf/tests/test_accounts.py::test_support_access_cannot_create_or_manage_accounts[control]`
33. `audioshelf/tests/test_accounts.py::test_support_access_cannot_create_or_manage_accounts[view]`
34. `audioshelf/tests/test_accounts.py::test_totp_and_trusted_browsers_belong_to_each_account`
35. `audioshelf/tests/test_artwork.py::test_album_cover_then_edition_then_spotify_fallback`
36. `audioshelf/tests/test_artwork.py::test_artwork_requires_password`
37. `audioshelf/tests/test_artwork.py::test_cache_can_be_rebuilt_without_losing_collection`
38. `audioshelf/tests/test_artwork.py::test_cache_cannot_live_inside_collection`
39. `audioshelf/tests/test_artwork.py::test_cover_preview_uses_fixed_source_and_never_caches[False]`
40. `audioshelf/tests/test_artwork.py::test_cover_preview_uses_fixed_source_and_never_caches[True]`
41. `audioshelf/tests/test_artwork.py::test_database_backup_excludes_metadata_cache`
42. `audioshelf/tests/test_artwork.py::test_default_album_cover_ignores_tracklist_edition_and_refreshes_old_cache`
43. `audioshelf/tests/test_artwork.py::test_download_hosts_and_size_are_restricted`
44. `audioshelf/tests/test_artwork.py::test_downloaded_artwork_is_cached_outside_collection`
45. `audioshelf/tests/test_artwork.py::test_explicit_cover_precedes_album_default_without_library_cache`
46. `audioshelf/tests/test_artwork.py::test_invalid_artwork_upload_leaves_previous_cover`
47. `audioshelf/tests/test_artwork.py::test_invalid_cover_size_never_downloads`
48. `audioshelf/tests/test_artwork.py::test_invalid_preview_identifier_never_reaches_downloader`
49. `audioshelf/tests/test_artwork.py::test_missing_cover_retries_after_spotify_connects`
50. `audioshelf/tests/test_artwork.py::test_removal_evicts_download_and_keeps_uploaded_cover`
51. `audioshelf/tests/test_artwork.py::test_schema_upgrade_removes_old_reproducible_cache`
52. `audioshelf/tests/test_artwork.py::test_selected_cover_is_cached_only_after_album_joins_shelf`
53. `audioshelf/tests/test_artwork.py::test_sized_cover_changes_invalidate_and_removal_evicts_variants`
54. `audioshelf/tests/test_artwork.py::test_sized_covers_preserve_aspect_cache_and_revalidate_privately[128]`
55. `audioshelf/tests/test_artwork.py::test_sized_covers_preserve_aspect_cache_and_revalidate_privately[320]`
56. `audioshelf/tests/test_artwork.py::test_sized_covers_preserve_aspect_cache_and_revalidate_privately[640]`
57. `audioshelf/tests/test_artwork.py::test_startup_purges_store_only_artwork`
58. `audioshelf/tests/test_artwork.py::test_store_browsing_never_saves_artwork`
59. `audioshelf/tests/test_artwork.py::test_uploaded_cover_survives_restart_and_reset`
60. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[amber]`
61. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[forest]`
62. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[high-contrast]`
63. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[midnight]`
64. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[monochrome]`
65. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[ocean]`
66. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[paper]`
67. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[plum]`
68. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[record-store]`
69. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[sunset]`
70. `audioshelf/tests/test_catalogue_diagnostics.py::test_amnesiac_alias_cannot_accept_kid_a_morning_bell_or_another_artist`
71. `audioshelf/tests/test_catalogue_diagnostics.py::test_amnesiac_printed_title_variants_verify_with_artist_and_duration[Dollars & Cents-Dollars and Cents-291000]`
72. `audioshelf/tests/test_catalogue_diagnostics.py::test_amnesiac_printed_title_variants_verify_with_artist_and_duration[Pull Pulk Revolving Doors-Pulk/Pull Revolving Doors-247000]`
73. `audioshelf/tests/test_catalogue_diagnostics.py::test_amnesiac_printed_title_variants_verify_with_artist_and_duration[The Morning Bell Amnesiac-Morning Bell/Amnesiac-194000]`
74. `audioshelf/tests/test_catalogue_diagnostics.py::test_beatles_core_catalogue_includes_soundtracks_excludes_regional_and_compilation`
75. `audioshelf/tests/test_catalogue_diagnostics.py::test_cover_selection_preserves_tracks_and_custom_cover_when_download_fails`
76. `audioshelf/tests/test_catalogue_diagnostics.py::test_dated_remix_is_rejected_while_stereo_mix_is_accepted`
77. `audioshelf/tests/test_catalogue_diagnostics.py::test_diagnostics_are_bounded_and_keep_only_latest_large_assessment`
78. `audioshelf/tests/test_catalogue_diagnostics.py::test_diagnostics_record_matching_decisions_and_errors_without_credentials`
79. `audioshelf/tests/test_catalogue_diagnostics.py::test_invalid_theme_does_not_partially_save_preferences`
80. `audioshelf/tests/test_catalogue_diagnostics.py::test_legacy_cassette_cover_uses_allowed_edition_without_changing_tracklist`
81. `audioshelf/tests/test_catalogue_diagnostics.py::test_non_album_series_cannot_be_selected`
82. `audioshelf/tests/test_catalogue_diagnostics.py::test_series_and_individual_overrides_work_for_other_artists_and_keep_shelf`
83. `audioshelf/tests/test_catalogue_diagnostics.py::test_successful_series_snapshot_survives_outage_for_any_artist`
84. `audioshelf/tests/test_catalogue_storage.py::test_backup_is_valid_and_excludes_pending_authorization`
85. `audioshelf/tests/test_catalogue_storage.py::test_changing_canonical_edition_clears_stale_mappings`
86. `audioshelf/tests/test_catalogue_storage.py::test_collaborative_album_belongs_to_both_credited_artists`
87. `audioshelf/tests/test_catalogue_storage.py::test_default_country_order_and_standard_edition_preference`
88. `audioshelf/tests/test_catalogue_storage.py::test_discography_excludes_minor_and_nonstudio_releases[Compilation]`
89. `audioshelf/tests/test_catalogue_storage.py::test_discography_excludes_minor_and_nonstudio_releases[DJ-mix]`
90. `audioshelf/tests/test_catalogue_storage.py::test_discography_excludes_minor_and_nonstudio_releases[Demo]`
91. `audioshelf/tests/test_catalogue_storage.py::test_discography_excludes_minor_and_nonstudio_releases[Live]`
92. `audioshelf/tests/test_catalogue_storage.py::test_discography_excludes_minor_and_nonstudio_releases[Remix]`
93. `audioshelf/tests/test_catalogue_storage.py::test_discography_excludes_minor_and_nonstudio_releases[Soundtrack]`
94. `audioshelf/tests/test_catalogue_storage.py::test_future_schema_does_not_get_downgraded`
95. `audioshelf/tests/test_catalogue_storage.py::test_manual_mapping_survives_automatic_reresolution`
96. `audioshelf/tests/test_catalogue_storage.py::test_original_two_disc_album_retains_both_discs`
97. `audioshelf/tests/test_catalogue_storage.py::test_shelf_persists_across_restart_and_remove_retains_mapping`
98. `audioshelf/tests/test_catalogue_storage.py::test_unresolved_manual_edition_can_be_fixed_by_automatic_matching`
99. `audioshelf/tests/test_generic_catalogues.py::test_catalogue_decisions_depend_on_membership_and_types_not_names[A film composer-Original score]`
100. `audioshelf/tests/test_generic_catalogues.py::test_catalogue_decisions_depend_on_membership_and_types_not_names[A new band-Second album]`
101. `audioshelf/tests/test_generic_catalogues.py::test_catalogue_decisions_depend_on_membership_and_types_not_names[An unfamiliar artist-Regional LP]`
102. `audioshelf/tests/test_generic_catalogues.py::test_catalogue_discovery_uses_artist_name_and_returns_only_release_group_series`
103. `audioshelf/tests/test_generic_catalogues.py::test_clearing_series_restores_generic_rules_and_keeps_shelf`
104. `audioshelf/tests/test_generic_catalogues.py::test_collaborative_catalogue_rules_do_not_depend_on_credit_order`
105. `audioshelf/tests/test_generic_catalogues.py::test_curated_rules_handle_soundtracks_for_every_artist[secondary0-True]`
106. `audioshelf/tests/test_generic_catalogues.py::test_curated_rules_handle_soundtracks_for_every_artist[secondary1-True]`
107. `audioshelf/tests/test_generic_catalogues.py::test_curated_rules_handle_soundtracks_for_every_artist[secondary2-False]`
108. `audioshelf/tests/test_generic_catalogues.py::test_curated_rules_handle_soundtracks_for_every_artist[secondary3-False]`
109. `audioshelf/tests/test_generic_catalogues.py::test_curated_rules_handle_soundtracks_for_every_artist[secondary4-False]`
110. `audioshelf/tests/test_generic_catalogues.py::test_explicit_catalogue_and_country_choices_survive_upgrade_and_restart`
111. `audioshelf/tests/test_generic_catalogues.py::test_no_artist_or_album_has_a_built_in_catalogue_exception`
112. `audioshelf/tests/test_generic_catalogues.py::test_old_cached_series_members_gain_a_persistent_snapshot`
113. `audioshelf/tests/test_generic_catalogues.py::test_series_snapshot_survives_restart_and_deleted_replaceable_cache`
114. `audioshelf/tests/test_generic_catalogues.py::test_unrelated_series_cannot_replace_selected_catalogue`
115. `audioshelf/tests/test_listening_links.py::test_authenticated_endpoint_and_no_cors_by_default`
116. `audioshelf/tests/test_listening_links.py::test_cors_limited_to_configured_origin`
117. `audioshelf/tests/test_listening_links.py::test_only_owned_records_and_song_album_links`
118. `audioshelf/tests/test_matching.py::test_alternative_recordings_not_silently_accepted[Opening (Demo)]`
119. `audioshelf/tests/test_matching.py::test_alternative_recordings_not_silently_accepted[Opening - Acoustic]`
120. `audioshelf/tests/test_matching.py::test_alternative_recordings_not_silently_accepted[Opening - Instrumental]`
121. `audioshelf/tests/test_matching.py::test_alternative_recordings_not_silently_accepted[Opening - Live]`
122. `audioshelf/tests/test_matching.py::test_alternative_recordings_not_silently_accepted[Opening - Radio Edit]`
123. `audioshelf/tests/test_matching.py::test_alternative_recordings_not_silently_accepted[Opening - Remix]`
124. `audioshelf/tests/test_matching.py::test_bonus_track_remaster_year_does_not_change_album_preference`
125. `audioshelf/tests/test_matching.py::test_clean_standard_edition_wins_equal_matching`
126. `audioshelf/tests/test_matching.py::test_dated_studio_mix_matches_but_dance_remix_still_rejected`
127. `audioshelf/tests/test_matching.py::test_deluxe_bonus_tracks_are_never_mapped`
128. `audioshelf/tests/test_matching.py::test_duplicate_titles_use_distinct_ordered_recordings`
129. `audioshelf/tests/test_matching.py::test_incomplete_recent_remaster_does_not_beat_complete_older_edition`
130. `audioshelf/tests/test_matching.py::test_latest_labelled_remaster_wins_even_with_bonus_tracks`
131. `audioshelf/tests/test_matching.py::test_missing_track_is_not_replaced_by_a_bonus_track`
132. `audioshelf/tests/test_matching.py::test_normalization_keeps_meaningful_version_labels`
133. `audioshelf/tests/test_matching.py::test_recent_reissue_date_does_not_prove_new_remaster`
134. `audioshelf/tests/test_matching.py::test_same_title_with_different_duration_needs_review`
135. `audioshelf/tests/test_matching.py::test_wrong_artist_and_unavailable_tracks_are_rejected`
136. `audioshelf/tests/test_musicbrainz_transport.py::test_artist_discography_paginates_filters_and_sorts`
137. `audioshelf/tests/test_musicbrainz_transport.py::test_first_tracklist_selection_and_group_validation`
138. `audioshelf/tests/test_musicbrainz_transport.py::test_lucene_special_characters_are_literal`
139. `audioshelf/tests/test_musicbrainz_transport.py::test_musicbrainz_requests_cache_and_wait_between_calls`
140. `audioshelf/tests/test_musicbrainz_transport.py::test_release_pagination_increments_actual_page_size`
141. `audioshelf/tests/test_now_playing.py::test_external_studio_album_opens_record_store_and_reuses_canonical_group`
142. `audioshelf/tests/test_now_playing.py::test_live_album_does_not_redirect_to_original_studio_album`
143. `audioshelf/tests/test_now_playing.py::test_now_playing_artwork_and_album_resolution_for_uncollected_record`
144. `audioshelf/tests/test_now_playing.py::test_same_title_different_artist_cannot_open_a_wrong_album`
145. `audioshelf/tests/test_now_playing.py::test_settings_default_and_skip_control_authorisation`
146. `audioshelf/tests/test_now_playing.py::test_spotify_album_images_are_selected_and_untrusted_urls_excluded`
147. `audioshelf/tests/test_now_playing.py::test_strip_edition_without_changing_canonical_album[Another Country-Another Country]`
148. `audioshelf/tests/test_now_playing.py::test_strip_edition_without_changing_canonical_album[Revolver (2022 Remaster)-Revolver]`
149. `audioshelf/tests/test_now_playing.py::test_strip_edition_without_changing_canonical_album[The Album (Deluxe Edition)-The Album]`
150. `audioshelf/tests/test_now_playing.py::test_strip_edition_without_changing_canonical_album[The Album - 2019 Remaster-The Album]`
151. `audioshelf/tests/test_now_playing.py::test_verified_studio_track_opens_its_own_shelf`
152. `audioshelf/tests/test_playback_handoff.py::test_cancel_replacement_revocation_and_expiry_prevent_dispatch[cancel]`
153. `audioshelf/tests/test_playback_handoff.py::test_cancel_replacement_revocation_and_expiry_prevent_dispatch[expire]`
154. `audioshelf/tests/test_playback_handoff.py::test_cancel_replacement_revocation_and_expiry_prevent_dispatch[replace]`
155. `audioshelf/tests/test_playback_handoff.py::test_cancel_replacement_revocation_and_expiry_prevent_dispatch[revoke]`
156. `audioshelf/tests/test_playback_handoff.py::test_cancellation_during_device_activation_prevents_queue`
157. `audioshelf/tests/test_playback_handoff.py::test_handoff_routes_validate_and_keep_status_private`
158. `audioshelf/tests/test_playback_handoff.py::test_revoking_real_session_cancels_server_job`
159. `audioshelf/tests/test_playback_handoff.py::test_waits_for_snapshot_phone_then_dispatches_exact_disc_once`
160. `audioshelf/tests/test_playback_handoff.py::test_worker_stops_on_spotify_errors_without_repeating_queue`
161. `audioshelf/tests/test_recording_metadata.py::test_any_artist_can_match_the_linked_recording_names[Other spelling - 2022 Mix]`
162. `audioshelf/tests/test_recording_metadata.py::test_any_artist_can_match_the_linked_recording_names[Other spelling]`
163. `audioshelf/tests/test_recording_metadata.py::test_any_artist_can_match_the_linked_recording_names[Recording title]`
164. `audioshelf/tests/test_recording_metadata.py::test_existing_shelf_enriches_unmatched_tracks_and_preserves_manual_corrections[False]`
165. `audioshelf/tests/test_recording_metadata.py::test_existing_shelf_enriches_unmatched_tracks_and_preserves_manual_corrections[True]`
166. `audioshelf/tests/test_recording_metadata.py::test_metadata_outage_keeps_saved_names_and_mappings`
167. `audioshelf/tests/test_recording_metadata.py::test_no_radiohead_specific_exception_remains`
168. `audioshelf/tests/test_recording_metadata.py::test_real_version_three_migration_preserves_collection_and_manual_mapping`
169. `audioshelf/tests/test_recording_metadata.py::test_recording_aliases_keep_identity_duration_and_version_safeguards[changes0-Other spelling]`
170. `audioshelf/tests/test_recording_metadata.py::test_recording_aliases_keep_identity_duration_and_version_safeguards[changes1-Other spelling]`
171. `audioshelf/tests/test_recording_metadata.py::test_recording_aliases_keep_identity_duration_and_version_safeguards[changes2-Other spelling]`
172. `audioshelf/tests/test_recording_metadata.py::test_recording_aliases_keep_identity_duration_and_version_safeguards[changes3-Other spelling]`
173. `audioshelf/tests/test_recording_metadata.py::test_recording_aliases_keep_identity_duration_and_version_safeguards[changes4-Other spelling]`
174. `audioshelf/tests/test_recording_metadata.py::test_recording_aliases_keep_identity_duration_and_version_safeguards[changes5-Other spelling - Live]`
175. `audioshelf/tests/test_recording_metadata.py::test_recording_aliases_keep_identity_duration_and_version_safeguards[changes6-Other spelling - Demo]`
176. `audioshelf/tests/test_recording_metadata.py::test_recording_aliases_keep_identity_duration_and_version_safeguards[changes7-Other spelling - 2022 Remix]`
177. `audioshelf/tests/test_recording_metadata.py::test_recording_metadata_survives_save_and_restart`
178. `audioshelf/tests/test_recording_metadata.py::test_verified_manual_track_does_not_trigger_recording_lookup`
179. `audioshelf/tests/test_recording_metadata.py::test_wrong_recording_response_cannot_supply_aliases`
180. `audioshelf/tests/test_release_filters.py::test_album_country_preference_does_not_change_other_albums`
181. `audioshelf/tests/test_release_filters.py::test_any_country_can_be_preferred_without_hidden_country_penalties[AU]`
182. `audioshelf/tests/test_release_filters.py::test_any_country_can_be_preferred_without_hidden_country_penalties[JP]`
183. `audioshelf/tests/test_release_filters.py::test_any_country_can_be_preferred_without_hidden_country_penalties[US]`
184. `audioshelf/tests/test_release_filters.py::test_browse_and_automatic_selection_skip_early_cassette`
185. `audioshelf/tests/test_release_filters.py::test_country_and_format_priorities_with_original_reissue_safeguard`
186. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats0-True]`
187. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats1-True]`
188. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats10-False]`
189. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats11-False]`
190. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats12-False]`
191. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats13-False]`
192. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats2-True]`
193. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats3-True]`
194. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats4-True]`
195. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats5-True]`
196. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats6-True]`
197. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats7-True]`
198. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats8-False]`
199. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats9-False]`
200. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[None-formats17-True]`
201. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[US-formats14-True]`
202. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[XE-formats16-True]`
203. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[XW-formats15-True]`
204. `audioshelf/tests/test_release_filters.py::test_direct_selection_cannot_bypass_filters_or_clear_mappings`
205. `audioshelf/tests/test_release_filters.py::test_filter_change_applies_to_cached_raw_pages`
206. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[None]`
207. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[value1]`
208. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[value2]`
209. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[value3]`
210. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[value4]`
211. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[value5]`
212. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[value6]`
213. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[value7]`
214. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[value8]`
215. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[value9]`
216. `audioshelf/tests/test_release_filters.py::test_multiple_release_events_include_gb_but_missing_area_does_not`
217. `audioshelf/tests/test_release_filters.py::test_no_country_preference_uses_date_and_format_equally_for_all_countries`
218. `audioshelf/tests/test_release_filters.py::test_no_matching_editions_has_settings_guidance_without_fallback`
219. `audioshelf/tests/test_release_filters.py::test_paginated_picker_filters_each_page_and_keeps_raw_cursor`
220. `audioshelf/tests/test_release_filters.py::test_preferences_persist_preserve_mappings_and_are_backed_up`
221. `audioshelf/tests/test_release_filters.py::test_settings_mutations_require_application_header`
222. `audioshelf/tests/test_release_filters.py::test_strict_country_setting_is_optional_and_validated`
223. `audioshelf/tests/test_release_filters.py::test_upgrade_from_version_two_keeps_collection_and_sets_defaults`
224. `audioshelf/tests/test_routes.py::test_callback_bad_state_does_not_connect`
225. `audioshelf/tests/test_routes.py::test_canonical_change_requires_confirmation`
226. `audioshelf/tests/test_routes.py::test_export_contains_library_without_tokens_or_oauth`
227. `audioshelf/tests/test_routes.py::test_ingress_assets_and_urls_are_prefixed`
228. `audioshelf/tests/test_routes.py::test_invalid_ids_rejected_without_remote_request`
229. `audioshelf/tests/test_routes.py::test_mutations_require_same_origin_custom_header`
230. `audioshelf/tests/test_routes.py::test_password_protection_and_spoofed_ingress_header`
231. `audioshelf/tests/test_routes.py::test_preferred_device_settings_validation_and_clear`
232. `audioshelf/tests/test_routes.py::test_shell_assets_share_content_version_and_update_worker_is_not_cached`
233. `audioshelf/tests/test_routes.py::test_shell_health_and_empty_shelf`
234. `audioshelf/tests/test_security.py::test_blank_password_locks_standalone_but_ingress_still_works`
235. `audioshelf/tests/test_security.py::test_blank_reset_option_does_not_disable_enrolled_factor`
236. `audioshelf/tests/test_security.py::test_cross_origin_mutation_is_rejected`
237. `audioshelf/tests/test_security.py::test_ha_option_recovers_lost_factor_and_preserves_password_spotify_and_collection`
238. `audioshelf/tests/test_security.py::test_ha_reset_is_once_per_changed_value_even_after_clearing_and_reenrolling`
239. `audioshelf/tests/test_security.py::test_home_assistant_can_recover_totp_without_factor_but_spoofed_header_cannot`
240. `audioshelf/tests/test_security.py::test_login_throttle_survives_restart_and_ignores_forwarded_ip`
241. `audioshelf/tests/test_security.py::test_password_change_invalidates_existing_sessions`
242. `audioshelf/tests/test_security.py::test_reset_option_rejects_invalid_configuration_without_disabling_factor[123]`
243. `audioshelf/tests/test_security.py::test_reset_option_rejects_invalid_configuration_without_disabling_factor[True]`
244. `audioshelf/tests/test_security.py::test_reset_option_rejects_invalid_configuration_without_disabling_factor[xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx]`
245. `audioshelf/tests/test_security.py::test_security_changes_require_fresh_owner_factor_even_on_trusted_browser`
246. `audioshelf/tests/test_security.py::test_sessions_and_factor_setup_survive_restarts_and_setup_expires`
247. `audioshelf/tests/test_security.py::test_setup_bruteforce_and_expiry_are_limited`
248. `audioshelf/tests/test_security.py::test_standard_sessions_are_secure_expire_and_logout_revokes`
249. `audioshelf/tests/test_security.py::test_support_disabled_by_default_and_secrets_not_in_collection`
250. `audioshelf/tests/test_security.py::test_support_master_toggle_revokes_existing_and_prevents_resurrection`
251. `audioshelf/tests/test_security.py::test_support_permissions_expiry_and_revocation_are_server_enforced[control]`
252. `audioshelf/tests/test_security.py::test_support_permissions_expiry_and_revocation_are_server_enforced[view]`
253. `audioshelf/tests/test_security.py::test_totp_matches_rfc_vector`
254. `audioshelf/tests/test_security.py::test_totp_requires_factor_rejects_replay_and_recovery_is_single_use`
255. `audioshelf/tests/test_security.py::test_trusted_browser_credential_is_revoked_on_logout`
256. `audioshelf/tests/test_security.py::test_trusted_browser_still_needs_password_and_is_revocable`
257. `audioshelf/tests/test_spotify_auth_playback.py::test_album_tracks_pagination_is_complete`
258. `audioshelf/tests/test_spotify_auth_playback.py::test_api_401_refreshes_once`
259. `audioshelf/tests/test_spotify_auth_playback.py::test_candidate_search_reaches_new_remaster_on_next_page`
260. `audioshelf/tests/test_spotify_auth_playback.py::test_disc_playback_excludes_other_discs_and_allows_unmapped_other_disc`
261. `audioshelf/tests/test_spotify_auth_playback.py::test_expired_oauth_is_consumed_without_exchange`
262. `audioshelf/tests/test_spotify_auth_playback.py::test_fallback_candidate_search_paginates_when_structured_search_is_empty`
263. `audioshelf/tests/test_spotify_auth_playback.py::test_invalid_disc_never_starts_playback[-1]`
264. `audioshelf/tests/test_spotify_auth_playback.py::test_invalid_disc_never_starts_playback[0]`
265. `audioshelf/tests/test_spotify_auth_playback.py::test_invalid_disc_never_starts_playback[1.5]`
266. `audioshelf/tests/test_spotify_auth_playback.py::test_invalid_disc_never_starts_playback[1]`
267. `audioshelf/tests/test_spotify_auth_playback.py::test_invalid_disc_never_starts_playback[3]`
268. `audioshelf/tests/test_spotify_auth_playback.py::test_invalid_disc_never_starts_playback[True]`
269. `audioshelf/tests/test_spotify_auth_playback.py::test_no_active_device_has_actionable_error`
270. `audioshelf/tests/test_spotify_auth_playback.py::test_no_active_player_with_multiple_or_restricted_devices_never_guesses[devices0]`
271. `audioshelf/tests/test_spotify_auth_playback.py::test_no_active_player_with_multiple_or_restricted_devices_never_guesses[devices1]`
272. `audioshelf/tests/test_spotify_auth_playback.py::test_no_active_player_with_multiple_or_restricted_devices_never_guesses[devices2]`
273. `audioshelf/tests/test_spotify_auth_playback.py::test_partial_mapping_cannot_play`
274. `audioshelf/tests/test_spotify_auth_playback.py::test_pkce_and_single_use_oauth_state`
275. `audioshelf/tests/test_spotify_auth_playback.py::test_play_route_requires_choice_even_when_a_speaker_is_active`
276. `audioshelf/tests/test_spotify_auth_playback.py::test_playback_sends_only_exact_track_uris_and_turns_off_shuffle_repeat`
277. `audioshelf/tests/test_spotify_auth_playback.py::test_preferred_device_transfer_preserves_exact_disc_queue`
278. `audioshelf/tests/test_spotify_auth_playback.py::test_preferred_device_transfer_timeout_never_starts_tracks`
279. `audioshelf/tests/test_spotify_auth_playback.py::test_refresh_preserves_old_refresh_token_and_survives_restart`
280. `audioshelf/tests/test_spotify_auth_playback.py::test_shuffle_not_acknowledged_does_not_start_album`
281. `audioshelf/tests/test_spotify_auth_playback.py::test_spotify_album_link_formats[aaaaaaaaaaaaaaaaaaaaaa]`
282. `audioshelf/tests/test_spotify_auth_playback.py::test_spotify_album_link_formats[https://open.spotify.com/album/aaaaaaaaaaaaaaaaaaaaaa?si=hello]`
283. `audioshelf/tests/test_spotify_auth_playback.py::test_spotify_album_link_formats[https://open.spotify.com/intl-de/album/aaaaaaaaaaaaaaaaaaaaaa]`
284. `audioshelf/tests/test_spotify_auth_playback.py::test_spotify_album_link_formats[spotify:album:aaaaaaaaaaaaaaaaaaaaaa]`
285. `audioshelf/tests/test_spotify_auth_playback.py::test_unavailable_or_ambiguous_preference_never_plays_elsewhere[devices0]`
286. `audioshelf/tests/test_spotify_auth_playback.py::test_unavailable_or_ambiguous_preference_never_plays_elsewhere[devices1]`
287. `audioshelf/tests/test_spotify_auth_playback.py::test_unavailable_or_ambiguous_preference_never_plays_elsewhere[devices2]`
288. `audioshelf/tests/test_spotify_auth_playback.py::test_unreviewed_tracklist_cannot_play`
289. `audioshelf/tests/test_spotify_auth_playback.py::test_untrusted_spotify_urls_not_fetched`
290. `audioshelf/tests/test_vinyl.py::test_interface_persists_and_invalid_choice_is_atomic`
291. `audioshelf/tests/test_vinyl.py::test_playback_does_not_misidentify_shared_or_uncollected_tracks`
292. `audioshelf/tests/test_vinyl.py::test_playback_tracks_spotify_pause_and_relinked_library_track`
293. `audioshelf/tests/test_vinyl.py::test_shelf_furniture_persists_and_rejects_invalid_choices`

## Other release checks

1. Browser flows: `tests/test_ui.cjs`, `tests/test_vinyl_ui.cjs` and `tests/test_accounts_ui.cjs`.
2. Python compilation and launcher shell syntax.
3. Browser JavaScript syntax.
4. Add-on container build.
5. Test inventory freshness, including changed test bodies and CI configuration.

CI verifies completeness and freshness, not whether a human judgement about redundancy is correct. Review changed tests for duplicate coverage, obsolete requirements and meaningful regressions before updating this list. Git history retains previous numbered lists, timestamps and review notes.
