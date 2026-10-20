# AudioShelf test inventory

Reviewed: 2026-10-20T17:32:40+01:00 (Europe/London).

Reviewed all 408 backend cases and the revised Android-helper VM test fixture after fixing a diagnostic-handoff cancellation before Spotify Play. The browser end-to-end regression explicitly closes the initial startup dialog, navigates to My Shelf, confirms server handoff is still waiting, then verifies the chosen disc is queued exactly once when the phone appears. Explicit Cancel playback instead terminates the job, with an allowlisted reason visible in owner and bearer-scoped helper feedback. The focused Android-helper test now models the playback-dialog contents separately from unrelated modal content, proving that a verified background result does not inadvertently dismiss a different open dialog. Android SDK and native UI changes were independently built/linted, and the real Pixel still requires follow-up testing.

<!-- inventory: {"reviewed_at":"2026-10-20T17:32:40+01:00","review_note":"Reviewed all 408 backend cases and the revised Android-helper VM test fixture after fixing a diagnostic-handoff cancellation before Spotify Play. The browser end-to-end regression explicitly closes the initial startup dialog, navigates to My Shelf, confirms server handoff is still waiting, then verifies the chosen disc is queued exactly once when the phone appears. Explicit Cancel playback instead terminates the job, with an allowlisted reason visible in owner and bearer-scoped helper feedback. The focused Android-helper test now models the playback-dialog contents separately from unrelated modal content, proving that a verified background result does not inadvertently dismiss a different open dialog. Android SDK and native UI changes were independently built/linted, and the real Pixel still requires follow-up testing.","source_sha256":"9bddfe678f42579b7769e65a16f28d00f89447779c9f22dcf0d6516fd60a31bd"} -->

## Backend cases (408)

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
37. `audioshelf/tests/test_artwork.py::test_busy_artwork_downloads_return_fast_503_without_blocking_health`
38. `audioshelf/tests/test_artwork.py::test_cache_can_be_rebuilt_without_losing_collection`
39. `audioshelf/tests/test_artwork.py::test_cache_cannot_live_inside_collection`
40. `audioshelf/tests/test_artwork.py::test_cover_preview_uses_fixed_source_and_never_caches[False]`
41. `audioshelf/tests/test_artwork.py::test_cover_preview_uses_fixed_source_and_never_caches[True]`
42. `audioshelf/tests/test_artwork.py::test_database_backup_excludes_metadata_cache`
43. `audioshelf/tests/test_artwork.py::test_default_album_cover_ignores_tracklist_edition_and_refreshes_old_cache`
44. `audioshelf/tests/test_artwork.py::test_digital_candidates_are_ranked_before_cd_and_cassette`
45. `audioshelf/tests/test_artwork.py::test_digital_front_cover_precedes_scans_and_preserves_manual_choice`
46. `audioshelf/tests/test_artwork.py::test_download_hosts_and_size_are_restricted`
47. `audioshelf/tests/test_artwork.py::test_downloaded_artwork_is_cached_outside_collection`
48. `audioshelf/tests/test_artwork.py::test_explicit_cover_precedes_album_default_without_library_cache`
49. `audioshelf/tests/test_artwork.py::test_forced_refresh_preserves_cached_cover_when_download_fails`
50. `audioshelf/tests/test_artwork.py::test_invalid_artwork_upload_leaves_previous_cover`
51. `audioshelf/tests/test_artwork.py::test_invalid_cover_size_never_downloads`
52. `audioshelf/tests/test_artwork.py::test_invalid_preview_identifier_never_reaches_downloader`
53. `audioshelf/tests/test_artwork.py::test_missing_cover_retries_after_spotify_connects`
54. `audioshelf/tests/test_artwork.py::test_removal_evicts_download_and_keeps_uploaded_cover`
55. `audioshelf/tests/test_artwork.py::test_schema_upgrade_removes_old_reproducible_cache`
56. `audioshelf/tests/test_artwork.py::test_selected_cover_is_cached_only_after_album_joins_shelf`
57. `audioshelf/tests/test_artwork.py::test_sized_cover_changes_invalidate_and_removal_evicts_variants`
58. `audioshelf/tests/test_artwork.py::test_sized_covers_preserve_aspect_cache_and_revalidate_privately[128]`
59. `audioshelf/tests/test_artwork.py::test_sized_covers_preserve_aspect_cache_and_revalidate_privately[320]`
60. `audioshelf/tests/test_artwork.py::test_sized_covers_preserve_aspect_cache_and_revalidate_privately[640]`
61. `audioshelf/tests/test_artwork.py::test_spotify_search_cannot_attach_wrong_artists_digital_cover`
62. `audioshelf/tests/test_artwork.py::test_startup_purges_store_only_artwork`
63. `audioshelf/tests/test_artwork.py::test_store_browsing_never_saves_artwork`
64. `audioshelf/tests/test_artwork.py::test_uploaded_cover_survives_restart_and_reset`
65. `audioshelf/tests/test_artwork_refresh.py::test_bulk_refresh_preserves_custom_covers_and_downloads_both_sides`
66. `audioshelf/tests/test_artwork_refresh.py::test_refresh_job_progress_survives_requests`
67. `audioshelf/tests/test_artwork_refresh.py::test_refresh_rejects_duplicate_running_jobs`
68. `audioshelf/tests/test_artwork_refresh.py::test_refresh_requires_authentication_and_nonempty_shelf`
69. `audioshelf/tests/test_back_artwork.py::test_back_artwork_falls_back_to_other_album_release_and_stores_sized_copies`
70. `audioshelf/tests/test_back_artwork.py::test_back_candidates_ignore_playback_format_filters`
71. `audioshelf/tests/test_back_artwork.py::test_back_cover_falls_back_to_canonical_release_not_release_group`
72. `audioshelf/tests/test_back_artwork.py::test_back_cover_is_cached_and_resized_separately`
73. `audioshelf/tests/test_back_artwork.py::test_back_cover_store_preview_is_never_persisted`
74. `audioshelf/tests/test_back_artwork.py::test_back_cover_uses_same_account_and_size_guards`
75. `audioshelf/tests/test_back_artwork.py::test_back_falls_back_to_500px_if_1200px_is_unavailable`
76. `audioshelf/tests/test_back_artwork.py::test_missing_back_cover_returns_404_not_front_placeholder`
77. `audioshelf/tests/test_back_artwork.py::test_missing_back_does_not_cache_store_preview`
78. `audioshelf/tests/test_back_artwork.py::test_reset_clears_back_images_and_metadata`
79. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[amber]`
80. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[forest]`
81. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[high-contrast]`
82. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[midnight]`
83. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[monochrome]`
84. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[ocean]`
85. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[paper]`
86. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[plum]`
87. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[record-store]`
88. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[sunset]`
89. `audioshelf/tests/test_catalogue_diagnostics.py::test_amnesiac_alias_cannot_accept_kid_a_morning_bell_or_another_artist`
90. `audioshelf/tests/test_catalogue_diagnostics.py::test_amnesiac_printed_title_variants_verify_with_artist_and_duration[Dollars & Cents-Dollars and Cents-291000]`
91. `audioshelf/tests/test_catalogue_diagnostics.py::test_amnesiac_printed_title_variants_verify_with_artist_and_duration[Pull Pulk Revolving Doors-Pulk/Pull Revolving Doors-247000]`
92. `audioshelf/tests/test_catalogue_diagnostics.py::test_amnesiac_printed_title_variants_verify_with_artist_and_duration[The Morning Bell Amnesiac-Morning Bell/Amnesiac-194000]`
93. `audioshelf/tests/test_catalogue_diagnostics.py::test_beatles_core_catalogue_includes_soundtracks_excludes_regional_and_compilation`
94. `audioshelf/tests/test_catalogue_diagnostics.py::test_cover_selection_preserves_tracks_and_custom_cover_when_download_fails`
95. `audioshelf/tests/test_catalogue_diagnostics.py::test_dated_remix_is_rejected_while_stereo_mix_is_accepted`
96. `audioshelf/tests/test_catalogue_diagnostics.py::test_diagnostics_are_bounded_and_keep_only_latest_large_assessment`
97. `audioshelf/tests/test_catalogue_diagnostics.py::test_diagnostics_record_matching_decisions_and_errors_without_credentials`
98. `audioshelf/tests/test_catalogue_diagnostics.py::test_invalid_theme_does_not_partially_save_preferences`
99. `audioshelf/tests/test_catalogue_diagnostics.py::test_legacy_cassette_cover_uses_allowed_edition_without_changing_tracklist`
100. `audioshelf/tests/test_catalogue_diagnostics.py::test_non_album_series_cannot_be_selected`
101. `audioshelf/tests/test_catalogue_diagnostics.py::test_series_and_individual_overrides_work_for_other_artists_and_keep_shelf`
102. `audioshelf/tests/test_catalogue_diagnostics.py::test_successful_series_snapshot_survives_outage_for_any_artist`
103. `audioshelf/tests/test_catalogue_storage.py::test_backup_is_valid_and_excludes_pending_authorization`
104. `audioshelf/tests/test_catalogue_storage.py::test_changing_canonical_edition_clears_stale_mappings`
105. `audioshelf/tests/test_catalogue_storage.py::test_collaborative_album_belongs_to_both_credited_artists`
106. `audioshelf/tests/test_catalogue_storage.py::test_default_country_order_and_standard_edition_preference`
107. `audioshelf/tests/test_catalogue_storage.py::test_discography_excludes_minor_and_nonstudio_releases[Compilation]`
108. `audioshelf/tests/test_catalogue_storage.py::test_discography_excludes_minor_and_nonstudio_releases[DJ-mix]`
109. `audioshelf/tests/test_catalogue_storage.py::test_discography_excludes_minor_and_nonstudio_releases[Demo]`
110. `audioshelf/tests/test_catalogue_storage.py::test_discography_excludes_minor_and_nonstudio_releases[Live]`
111. `audioshelf/tests/test_catalogue_storage.py::test_discography_excludes_minor_and_nonstudio_releases[Remix]`
112. `audioshelf/tests/test_catalogue_storage.py::test_discography_excludes_minor_and_nonstudio_releases[Soundtrack]`
113. `audioshelf/tests/test_catalogue_storage.py::test_future_schema_does_not_get_downgraded`
114. `audioshelf/tests/test_catalogue_storage.py::test_manual_mapping_survives_automatic_reresolution`
115. `audioshelf/tests/test_catalogue_storage.py::test_original_two_disc_album_retains_both_discs`
116. `audioshelf/tests/test_catalogue_storage.py::test_shelf_persists_across_restart_and_remove_retains_mapping`
117. `audioshelf/tests/test_catalogue_storage.py::test_unresolved_manual_edition_can_be_fixed_by_automatic_matching`
118. `audioshelf/tests/test_generic_catalogues.py::test_catalogue_decisions_depend_on_membership_and_types_not_names[A film composer-Original score]`
119. `audioshelf/tests/test_generic_catalogues.py::test_catalogue_decisions_depend_on_membership_and_types_not_names[A new band-Second album]`
120. `audioshelf/tests/test_generic_catalogues.py::test_catalogue_decisions_depend_on_membership_and_types_not_names[An unfamiliar artist-Regional LP]`
121. `audioshelf/tests/test_generic_catalogues.py::test_catalogue_discovery_uses_artist_name_and_returns_only_release_group_series`
122. `audioshelf/tests/test_generic_catalogues.py::test_clearing_series_restores_generic_rules_and_keeps_shelf`
123. `audioshelf/tests/test_generic_catalogues.py::test_collaborative_catalogue_rules_do_not_depend_on_credit_order`
124. `audioshelf/tests/test_generic_catalogues.py::test_curated_rules_handle_soundtracks_for_every_artist[secondary0-True]`
125. `audioshelf/tests/test_generic_catalogues.py::test_curated_rules_handle_soundtracks_for_every_artist[secondary1-True]`
126. `audioshelf/tests/test_generic_catalogues.py::test_curated_rules_handle_soundtracks_for_every_artist[secondary2-False]`
127. `audioshelf/tests/test_generic_catalogues.py::test_curated_rules_handle_soundtracks_for_every_artist[secondary3-False]`
128. `audioshelf/tests/test_generic_catalogues.py::test_curated_rules_handle_soundtracks_for_every_artist[secondary4-False]`
129. `audioshelf/tests/test_generic_catalogues.py::test_explicit_catalogue_and_country_choices_survive_upgrade_and_restart`
130. `audioshelf/tests/test_generic_catalogues.py::test_no_artist_or_album_has_a_built_in_catalogue_exception`
131. `audioshelf/tests/test_generic_catalogues.py::test_old_cached_series_members_gain_a_persistent_snapshot`
132. `audioshelf/tests/test_generic_catalogues.py::test_series_snapshot_survives_restart_and_deleted_replaceable_cache`
133. `audioshelf/tests/test_generic_catalogues.py::test_unrelated_series_cannot_replace_selected_catalogue`
134. `audioshelf/tests/test_lastfm_import.py::test_import_adds_existing_group_without_duplicates_and_reports_skips`
135. `audioshelf/tests/test_lastfm_import.py::test_lastfm_edition_and_artist_aliases`
136. `audioshelf/tests/test_lastfm_import.py::test_lastfm_exact_match_rejects_ambiguity_and_wrong_artist`
137. `audioshelf/tests/test_lastfm_import.py::test_lastfm_import_separated_per_collection`
138. `audioshelf/tests/test_lastfm_import.py::test_lastfm_looks_up_edition_by_artist_instead_of_title_only`
139. `audioshelf/tests/test_lastfm_import.py::test_lastfm_opt_in_credentials_public_history_and_exact_selection`
140. `audioshelf/tests/test_lastfm_import.py::test_lastfm_self_titled_album_remains_ambiguous`
141. `audioshelf/tests/test_listening_links.py::test_authenticated_endpoint_and_no_cors_by_default`
142. `audioshelf/tests/test_listening_links.py::test_cors_limited_to_configured_origin`
143. `audioshelf/tests/test_listening_links.py::test_only_owned_records_and_song_album_links`
144. `audioshelf/tests/test_matching.py::test_acoustic_version_label_matches_same_acoustic_recording_but_not_studio`
145. `audioshelf/tests/test_matching.py::test_album_version_with_interlude_needs_same_full_audio_and_independent_album_context`
146. `audioshelf/tests/test_matching.py::test_alternative_recordings_not_silently_accepted[Opening (Demo)]`
147. `audioshelf/tests/test_matching.py::test_alternative_recordings_not_silently_accepted[Opening - Acoustic]`
148. `audioshelf/tests/test_matching.py::test_alternative_recordings_not_silently_accepted[Opening - Instrumental]`
149. `audioshelf/tests/test_matching.py::test_alternative_recordings_not_silently_accepted[Opening - Live]`
150. `audioshelf/tests/test_matching.py::test_alternative_recordings_not_silently_accepted[Opening - Radio Edit]`
151. `audioshelf/tests/test_matching.py::test_alternative_recordings_not_silently_accepted[Opening - Remix]`
152. `audioshelf/tests/test_matching.py::test_bonus_track_remaster_year_does_not_change_album_preference`
153. `audioshelf/tests/test_matching.py::test_clean_standard_edition_wins_equal_matching`
154. `audioshelf/tests/test_matching.py::test_compact_track_titles_accept_spacing_but_not_reordered_words`
155. `audioshelf/tests/test_matching.py::test_contextual_minor_title_change_must_have_unique_position_duration_and_artist`
156. `audioshelf/tests/test_matching.py::test_dated_studio_mix_matches_but_dance_remix_still_rejected`
157. `audioshelf/tests/test_matching.py::test_deluxe_bonus_tracks_are_never_mapped`
158. `audioshelf/tests/test_matching.py::test_descriptive_spotify_parody_source_credits_preserve_full_album`
159. `audioshelf/tests/test_matching.py::test_duplicate_titles_use_distinct_ordered_recordings`
160. `audioshelf/tests/test_matching.py::test_edition_labels_and_part_abbreviations_are_same_recording`
161. `audioshelf/tests/test_matching.py::test_editorial_subtitles_use_main_title_and_duration_but_not_versions`
162. `audioshelf/tests/test_matching.py::test_extended_combined_track_title_requires_duration_artist_and_version_agreement`
163. `audioshelf/tests/test_matching.py::test_featured_artist_credit_requires_primary_artist_and_guest_evidence`
164. `audioshelf/tests/test_matching.py::test_full_album_with_editorial_subtitles_is_verified_in_canonical_order`
165. `audioshelf/tests/test_matching.py::test_full_length_hidden_track_composites_never_substitute_the_short_single`
166. `audioshelf/tests/test_matching.py::test_hidden_label_can_match_contiguous_recording_but_not_erase_separate_musicbrainz_track`
167. `audioshelf/tests/test_matching.py::test_incomplete_recent_remaster_does_not_beat_complete_older_edition`
168. `audioshelf/tests/test_matching.py::test_latest_labelled_remaster_wins_even_with_bonus_tracks`
169. `audioshelf/tests/test_matching.py::test_missing_track_is_not_replaced_by_a_bonus_track`
170. `audioshelf/tests/test_matching.py::test_mono_stereo_labels_do_not_erase_live_or_remix_identity`
171. `audioshelf/tests/test_matching.py::test_normalization_keeps_meaningful_version_labels`
172. `audioshelf/tests/test_matching.py::test_numbered_part_roman_numerals_match_arabic_spotify_titles`
173. `audioshelf/tests/test_matching.py::test_parody_annotation_does_not_erase_recording_versions_or_unplayable`
174. `audioshelf/tests/test_matching.py::test_punchup_editorial_subtitle_accepts_compound_spacing_not_different_song`
175. `audioshelf/tests/test_matching.py::test_recent_reissue_date_does_not_prove_new_remaster`
176. `audioshelf/tests/test_matching.py::test_same_title_with_different_duration_needs_review`
177. `audioshelf/tests/test_matching.py::test_short_original_song_prefix_is_only_verifiable_in_strict_album_context`
178. `audioshelf/tests/test_matching.py::test_shortened_album_medley_requires_matching_artist_duration_and_position`
179. `audioshelf/tests/test_matching.py::test_single_duration_discrepancy_can_be_corroborated_by_entire_album`
180. `audioshelf/tests/test_matching.py::test_two_spotify_performers_satisfy_a_joint_musicbrainz_artist_but_not_solo`
181. `audioshelf/tests/test_matching.py::test_wrong_artist_and_unavailable_tracks_are_rejected`
182. `audioshelf/tests/test_musicbrainz_transport.py::test_artist_discography_paginates_filters_and_sorts`
183. `audioshelf/tests/test_musicbrainz_transport.py::test_first_tracklist_selection_and_group_validation`
184. `audioshelf/tests/test_musicbrainz_transport.py::test_lucene_special_characters_are_literal`
185. `audioshelf/tests/test_musicbrainz_transport.py::test_musicbrainz_requests_cache_and_wait_between_calls`
186. `audioshelf/tests/test_musicbrainz_transport.py::test_release_pagination_increments_actual_page_size`
187. `audioshelf/tests/test_now_playing.py::test_external_studio_album_opens_record_store_and_reuses_canonical_group`
188. `audioshelf/tests/test_now_playing.py::test_live_album_does_not_redirect_to_original_studio_album`
189. `audioshelf/tests/test_now_playing.py::test_now_playing_artwork_and_album_resolution_for_uncollected_record`
190. `audioshelf/tests/test_now_playing.py::test_same_title_different_artist_cannot_open_a_wrong_album`
191. `audioshelf/tests/test_now_playing.py::test_settings_default_and_skip_control_authorisation`
192. `audioshelf/tests/test_now_playing.py::test_spotify_album_images_are_selected_and_untrusted_urls_excluded`
193. `audioshelf/tests/test_now_playing.py::test_strip_edition_without_changing_canonical_album[Another Country-Another Country]`
194. `audioshelf/tests/test_now_playing.py::test_strip_edition_without_changing_canonical_album[Revolver (2022 Remaster)-Revolver]`
195. `audioshelf/tests/test_now_playing.py::test_strip_edition_without_changing_canonical_album[The Album (Deluxe Edition)-The Album]`
196. `audioshelf/tests/test_now_playing.py::test_strip_edition_without_changing_canonical_album[The Album - 2019 Remaster-The Album]`
197. `audioshelf/tests/test_now_playing.py::test_verified_studio_track_opens_its_own_shelf`
198. `audioshelf/tests/test_playable_releases.py::test_change_tracklist_is_atomic_and_only_accepts_full_match`
199. `audioshelf/tests/test_playable_releases.py::test_invalid_cursor_is_rejected`
200. `audioshelf/tests/test_playable_releases.py::test_later_batched_editions_can_be_discovered`
201. `audioshelf/tests/test_playable_releases.py::test_matching_original_album_uses_isrc_for_unlabelled_version_not_shortcuts`
202. `audioshelf/tests/test_playable_releases.py::test_original_musicbrainz_tracklist_ignores_spotify_bonus_tracks_even_in_labelled_editions[10-11-Deluxe Edition]`
203. `audioshelf/tests/test_playable_releases.py::test_original_musicbrainz_tracklist_ignores_spotify_bonus_tracks_even_in_labelled_editions[14-2-Explicit Version]`
204. `audioshelf/tests/test_playable_releases.py::test_picker_hides_partially_matched_musicbrainz_edition_and_does_not_mutate`
205. `audioshelf/tests/test_playable_releases.py::test_shelf_add_commits_whole_album_mapping_and_requires_review`
206. `audioshelf/tests/test_playable_releases.py::test_shelf_add_rejects_incomplete_mapping_before_mutation`
207. `audioshelf/tests/test_playable_releases.py::test_unconnected_spotify_has_clear_feedback_without_mutation`
208. `audioshelf/tests/test_playback_handoff.py::test_ambiguous_play_transport_failure_never_claims_accepted_or_requeues`
209. `audioshelf/tests/test_playback_handoff.py::test_cancel_reason_identifies_explicit_user_action_but_not_untrusted_text`
210. `audioshelf/tests/test_playback_handoff.py::test_cancel_replacement_revocation_and_expiry_prevent_dispatch[cancel]`
211. `audioshelf/tests/test_playback_handoff.py::test_cancel_replacement_revocation_and_expiry_prevent_dispatch[expire]`
212. `audioshelf/tests/test_playback_handoff.py::test_cancel_replacement_revocation_and_expiry_prevent_dispatch[replace]`
213. `audioshelf/tests/test_playback_handoff.py::test_cancel_replacement_revocation_and_expiry_prevent_dispatch[revoke]`
214. `audioshelf/tests/test_playback_handoff.py::test_cancellation_during_device_activation_prevents_queue`
215. `audioshelf/tests/test_playback_handoff.py::test_cold_start_polling_is_fast_only_during_initial_phone_discovery`
216. `audioshelf/tests/test_playback_handoff.py::test_handoff_routes_validate_and_keep_status_private`
217. `audioshelf/tests/test_playback_handoff.py::test_helper_feedback_includes_bounded_track_mismatch_without_private_payloads`
218. `audioshelf/tests/test_playback_handoff.py::test_helper_feedback_remains_readable_while_spotify_play_is_blocked`
219. `audioshelf/tests/test_playback_handoff.py::test_helper_token_revoked_on_job_replacement_and_cannot_access_other_jobs`
220. `audioshelf/tests/test_playback_handoff.py::test_new_job_replaces_prior_job_with_safe_reason`
221. `audioshelf/tests/test_playback_handoff.py::test_owner_snapshot_exposes_safe_progress_but_not_playback_capability`
222. `audioshelf/tests/test_playback_handoff.py::test_play_acceptance_is_published_before_device_and_track_confirmation`
223. `audioshelf/tests/test_playback_handoff.py::test_revoking_real_session_cancels_server_job`
224. `audioshelf/tests/test_playback_handoff.py::test_spotify_player_confirmation_polling_is_fast_after_play_then_tapers`
225. `audioshelf/tests/test_playback_handoff.py::test_verification_observation_distinguishes_old_track_missing_item_and_linked_track`
226. `audioshelf/tests/test_playback_handoff.py::test_waits_for_snapshot_phone_then_dispatches_exact_disc_once`
227. `audioshelf/tests/test_playback_handoff.py::test_worker_stops_on_spotify_errors_without_repeating_queue`
228. `audioshelf/tests/test_playback_handoff.py::test_wrong_track_or_wrong_device_never_falsely_confirm_playback`
229. `audioshelf/tests/test_recording_metadata.py::test_any_artist_can_match_the_linked_recording_names[Other spelling - 2022 Mix]`
230. `audioshelf/tests/test_recording_metadata.py::test_any_artist_can_match_the_linked_recording_names[Other spelling]`
231. `audioshelf/tests/test_recording_metadata.py::test_any_artist_can_match_the_linked_recording_names[Recording title]`
232. `audioshelf/tests/test_recording_metadata.py::test_existing_shelf_enriches_unmatched_tracks_and_preserves_manual_corrections[False]`
233. `audioshelf/tests/test_recording_metadata.py::test_existing_shelf_enriches_unmatched_tracks_and_preserves_manual_corrections[True]`
234. `audioshelf/tests/test_recording_metadata.py::test_metadata_outage_keeps_saved_names_and_mappings`
235. `audioshelf/tests/test_recording_metadata.py::test_no_radiohead_specific_exception_remains`
236. `audioshelf/tests/test_recording_metadata.py::test_real_version_three_migration_preserves_collection_and_manual_mapping`
237. `audioshelf/tests/test_recording_metadata.py::test_recording_aliases_keep_identity_duration_and_version_safeguards[changes0-Other spelling]`
238. `audioshelf/tests/test_recording_metadata.py::test_recording_aliases_keep_identity_duration_and_version_safeguards[changes1-Other spelling]`
239. `audioshelf/tests/test_recording_metadata.py::test_recording_aliases_keep_identity_duration_and_version_safeguards[changes2-Other spelling]`
240. `audioshelf/tests/test_recording_metadata.py::test_recording_aliases_keep_identity_duration_and_version_safeguards[changes3-Other spelling]`
241. `audioshelf/tests/test_recording_metadata.py::test_recording_aliases_keep_identity_duration_and_version_safeguards[changes4-Other spelling]`
242. `audioshelf/tests/test_recording_metadata.py::test_recording_aliases_keep_identity_duration_and_version_safeguards[changes5-Other spelling - Live]`
243. `audioshelf/tests/test_recording_metadata.py::test_recording_aliases_keep_identity_duration_and_version_safeguards[changes6-Other spelling - Demo]`
244. `audioshelf/tests/test_recording_metadata.py::test_recording_aliases_keep_identity_duration_and_version_safeguards[changes7-Other spelling - 2022 Remix]`
245. `audioshelf/tests/test_recording_metadata.py::test_recording_metadata_survives_save_and_restart`
246. `audioshelf/tests/test_recording_metadata.py::test_verified_manual_track_does_not_trigger_recording_lookup`
247. `audioshelf/tests/test_recording_metadata.py::test_wrong_recording_response_cannot_supply_aliases`
248. `audioshelf/tests/test_release_filters.py::test_album_country_preference_does_not_change_other_albums`
249. `audioshelf/tests/test_release_filters.py::test_ambiguous_track_count_without_corrobation_preserves_old_selection_order`
250. `audioshelf/tests/test_release_filters.py::test_any_country_can_be_preferred_without_hidden_country_penalties[AU]`
251. `audioshelf/tests/test_release_filters.py::test_any_country_can_be_preferred_without_hidden_country_penalties[JP]`
252. `audioshelf/tests/test_release_filters.py::test_any_country_can_be_preferred_without_hidden_country_penalties[US]`
253. `audioshelf/tests/test_release_filters.py::test_automatic_import_prefers_common_original_track_count_without_changing_preferences`
254. `audioshelf/tests/test_release_filters.py::test_browse_and_automatic_selection_skip_early_cassette`
255. `audioshelf/tests/test_release_filters.py::test_country_and_format_priorities_with_original_reissue_safeguard`
256. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats0-True]`
257. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats1-True]`
258. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats10-False]`
259. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats11-False]`
260. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats12-False]`
261. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats13-False]`
262. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats2-True]`
263. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats3-True]`
264. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats4-True]`
265. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats5-True]`
266. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats6-True]`
267. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats7-True]`
268. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats8-False]`
269. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats9-False]`
270. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[None-formats17-True]`
271. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[US-formats14-True]`
272. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[XE-formats16-True]`
273. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[XW-formats15-True]`
274. `audioshelf/tests/test_release_filters.py::test_direct_selection_cannot_bypass_filters_or_clear_mappings`
275. `audioshelf/tests/test_release_filters.py::test_filter_change_applies_to_cached_raw_pages`
276. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[None]`
277. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[value1]`
278. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[value2]`
279. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[value3]`
280. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[value4]`
281. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[value5]`
282. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[value6]`
283. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[value7]`
284. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[value8]`
285. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[value9]`
286. `audioshelf/tests/test_release_filters.py::test_multiple_release_events_include_gb_but_missing_area_does_not`
287. `audioshelf/tests/test_release_filters.py::test_no_country_preference_uses_date_and_format_equally_for_all_countries`
288. `audioshelf/tests/test_release_filters.py::test_no_matching_editions_has_settings_guidance_without_fallback`
289. `audioshelf/tests/test_release_filters.py::test_paginated_picker_filters_each_page_and_keeps_raw_cursor`
290. `audioshelf/tests/test_release_filters.py::test_preferences_persist_preserve_mappings_and_are_backed_up`
291. `audioshelf/tests/test_release_filters.py::test_settings_mutations_require_application_header`
292. `audioshelf/tests/test_release_filters.py::test_strict_country_setting_is_optional_and_validated`
293. `audioshelf/tests/test_release_filters.py::test_upgrade_from_version_two_keeps_collection_and_sets_defaults`
294. `audioshelf/tests/test_routes.py::test_callback_bad_state_does_not_connect`
295. `audioshelf/tests/test_routes.py::test_canonical_change_requires_confirmation`
296. `audioshelf/tests/test_routes.py::test_export_contains_library_without_tokens_or_oauth`
297. `audioshelf/tests/test_routes.py::test_ingress_assets_and_urls_are_prefixed`
298. `audioshelf/tests/test_routes.py::test_invalid_ids_rejected_without_remote_request`
299. `audioshelf/tests/test_routes.py::test_mutations_require_same_origin_custom_header`
300. `audioshelf/tests/test_routes.py::test_password_protection_and_spoofed_ingress_header`
301. `audioshelf/tests/test_routes.py::test_preferred_device_settings_validation_and_clear`
302. `audioshelf/tests/test_routes.py::test_shell_assets_share_content_version_and_update_worker_is_not_cached`
303. `audioshelf/tests/test_routes.py::test_shell_health_and_empty_shelf`
304. `audioshelf/tests/test_security.py::test_blank_password_locks_standalone_but_ingress_still_works`
305. `audioshelf/tests/test_security.py::test_blank_reset_option_does_not_disable_enrolled_factor`
306. `audioshelf/tests/test_security.py::test_cross_origin_mutation_is_rejected`
307. `audioshelf/tests/test_security.py::test_ha_option_recovers_lost_factor_and_preserves_password_spotify_and_collection`
308. `audioshelf/tests/test_security.py::test_ha_reset_is_once_per_changed_value_even_after_clearing_and_reenrolling`
309. `audioshelf/tests/test_security.py::test_home_assistant_can_recover_totp_without_factor_but_spoofed_header_cannot`
310. `audioshelf/tests/test_security.py::test_login_throttle_survives_restart_and_ignores_forwarded_ip`
311. `audioshelf/tests/test_security.py::test_password_change_invalidates_existing_sessions`
312. `audioshelf/tests/test_security.py::test_reset_option_rejects_invalid_configuration_without_disabling_factor[123]`
313. `audioshelf/tests/test_security.py::test_reset_option_rejects_invalid_configuration_without_disabling_factor[True]`
314. `audioshelf/tests/test_security.py::test_reset_option_rejects_invalid_configuration_without_disabling_factor[xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx]`
315. `audioshelf/tests/test_security.py::test_security_changes_require_fresh_owner_factor_even_on_trusted_browser`
316. `audioshelf/tests/test_security.py::test_sessions_and_factor_setup_survive_restarts_and_setup_expires`
317. `audioshelf/tests/test_security.py::test_setup_bruteforce_and_expiry_are_limited`
318. `audioshelf/tests/test_security.py::test_standard_sessions_are_secure_expire_and_logout_revokes`
319. `audioshelf/tests/test_security.py::test_support_disabled_by_default_and_secrets_not_in_collection`
320. `audioshelf/tests/test_security.py::test_support_master_toggle_revokes_existing_and_prevents_resurrection`
321. `audioshelf/tests/test_security.py::test_support_permissions_expiry_and_revocation_are_server_enforced[control]`
322. `audioshelf/tests/test_security.py::test_support_permissions_expiry_and_revocation_are_server_enforced[view]`
323. `audioshelf/tests/test_security.py::test_totp_matches_rfc_vector`
324. `audioshelf/tests/test_security.py::test_totp_requires_factor_rejects_replay_and_recovery_is_single_use`
325. `audioshelf/tests/test_security.py::test_trusted_browser_credential_is_revoked_on_logout`
326. `audioshelf/tests/test_security.py::test_trusted_browser_still_needs_password_and_is_revocable`
327. `audioshelf/tests/test_shelf_edition_audit.py::test_audit_abstains_on_conflicting_origins_instead_of_shortest_guess`
328. `audioshelf/tests/test_shelf_edition_audit.py::test_audit_accepts_genuine_standard_double_album`
329. `audioshelf/tests/test_shelf_edition_audit.py::test_audit_does_not_infer_count_from_single_original_or_later_editions`
330. `audioshelf/tests/test_shelf_edition_audit.py::test_audit_flags_bonus_editions_without_modifying_original`
331. `audioshelf/tests/test_shelf_edition_audit.py::test_audit_flags_deluxe_label_even_without_reference_and_preserves_original`
332. `audioshelf/tests/test_shelf_edition_audit.py::test_audit_marks_albums_without_selected_tracklists_as_unresolved`
333. `audioshelf/tests/test_shelf_edition_audit.py::test_edition_audit_api_runs_across_entire_shelf_without_mutating`
334. `audioshelf/tests/test_spotify_auth_playback.py::test_album_tracks_pagination_is_complete`
335. `audioshelf/tests/test_spotify_auth_playback.py::test_api_401_refreshes_once`
336. `audioshelf/tests/test_spotify_auth_playback.py::test_candidate_search_reaches_new_remaster_on_next_page`
337. `audioshelf/tests/test_spotify_auth_playback.py::test_disc_playback_excludes_other_discs_and_allows_unmapped_other_disc`
338. `audioshelf/tests/test_spotify_auth_playback.py::test_expired_oauth_is_consumed_without_exchange`
339. `audioshelf/tests/test_spotify_auth_playback.py::test_fallback_candidate_search_paginates_when_structured_search_is_empty`
340. `audioshelf/tests/test_spotify_auth_playback.py::test_invalid_disc_never_starts_playback[-1]`
341. `audioshelf/tests/test_spotify_auth_playback.py::test_invalid_disc_never_starts_playback[0]`
342. `audioshelf/tests/test_spotify_auth_playback.py::test_invalid_disc_never_starts_playback[1.5]`
343. `audioshelf/tests/test_spotify_auth_playback.py::test_invalid_disc_never_starts_playback[1]`
344. `audioshelf/tests/test_spotify_auth_playback.py::test_invalid_disc_never_starts_playback[3]`
345. `audioshelf/tests/test_spotify_auth_playback.py::test_invalid_disc_never_starts_playback[True]`
346. `audioshelf/tests/test_spotify_auth_playback.py::test_labelled_album_search_finds_explicit_and_regional_editions_without_special_cases`
347. `audioshelf/tests/test_spotify_auth_playback.py::test_no_active_device_has_actionable_error`
348. `audioshelf/tests/test_spotify_auth_playback.py::test_no_active_player_with_multiple_or_restricted_devices_never_guesses[devices0]`
349. `audioshelf/tests/test_spotify_auth_playback.py::test_no_active_player_with_multiple_or_restricted_devices_never_guesses[devices1]`
350. `audioshelf/tests/test_spotify_auth_playback.py::test_no_active_player_with_multiple_or_restricted_devices_never_guesses[devices2]`
351. `audioshelf/tests/test_spotify_auth_playback.py::test_partial_mapping_cannot_play`
352. `audioshelf/tests/test_spotify_auth_playback.py::test_pkce_and_single_use_oauth_state`
353. `audioshelf/tests/test_spotify_auth_playback.py::test_play_route_requires_choice_even_when_a_speaker_is_active`
354. `audioshelf/tests/test_spotify_auth_playback.py::test_playback_sends_only_exact_track_uris_and_turns_off_shuffle_repeat`
355. `audioshelf/tests/test_spotify_auth_playback.py::test_preferred_device_transfer_preserves_exact_disc_queue`
356. `audioshelf/tests/test_spotify_auth_playback.py::test_preferred_device_transfer_timeout_never_starts_tracks`
357. `audioshelf/tests/test_spotify_auth_playback.py::test_refresh_preserves_old_refresh_token_and_survives_restart`
358. `audioshelf/tests/test_spotify_auth_playback.py::test_shuffle_not_acknowledged_does_not_start_album`
359. `audioshelf/tests/test_spotify_auth_playback.py::test_spotify_album_link_formats[aaaaaaaaaaaaaaaaaaaaaa]`
360. `audioshelf/tests/test_spotify_auth_playback.py::test_spotify_album_link_formats[https://open.spotify.com/album/aaaaaaaaaaaaaaaaaaaaaa?si=hello]`
361. `audioshelf/tests/test_spotify_auth_playback.py::test_spotify_album_link_formats[https://open.spotify.com/intl-de/album/aaaaaaaaaaaaaaaaaaaaaa]`
362. `audioshelf/tests/test_spotify_auth_playback.py::test_spotify_album_link_formats[spotify:album:aaaaaaaaaaaaaaaaaaaaaa]`
363. `audioshelf/tests/test_spotify_auth_playback.py::test_unavailable_or_ambiguous_preference_never_plays_elsewhere[devices0]`
364. `audioshelf/tests/test_spotify_auth_playback.py::test_unavailable_or_ambiguous_preference_never_plays_elsewhere[devices1]`
365. `audioshelf/tests/test_spotify_auth_playback.py::test_unavailable_or_ambiguous_preference_never_plays_elsewhere[devices2]`
366. `audioshelf/tests/test_spotify_auth_playback.py::test_unreviewed_tracklist_cannot_play`
367. `audioshelf/tests/test_spotify_auth_playback.py::test_untrusted_spotify_urls_not_fetched`
368. `audioshelf/tests/test_spotify_matching.py::test_background_import_prefers_original_tracklist_even_with_spotify_deluxe_bonus_tracks`
369. `audioshelf/tests/test_spotify_matching.py::test_background_verifies_alternate_release_after_unplayable_first_release`
370. `audioshelf/tests/test_spotify_matching.py::test_bulk_import_rejects_fully_playable_deluxe_if_two_originals_disagree`
371. `audioshelf/tests/test_spotify_matching.py::test_bulk_routes_restrict_access_and_expose_copyable_report`
372. `audioshelf/tests/test_spotify_matching.py::test_invalid_manual_track_choices_are_replaced_with_complete_edition`
373. `audioshelf/tests/test_spotify_matching.py::test_partial_candidates_are_reported_and_never_persisted`
374. `audioshelf/tests/test_spotify_matching.py::test_removed_or_fixed_album_drops_from_failure_report`
375. `audioshelf/tests/test_spotify_matching.py::test_report_distinguishes_out_of_order_track_from_arbitrary_fallback`
376. `audioshelf/tests/test_spotify_matching.py::test_reviewed_canonical_edition_remains_reviewed_when_fully_matched`
377. `audioshelf/tests/test_spotify_matching.py::test_reviewed_manual_edition_without_spotify_match_is_wiped`
378. `audioshelf/tests/test_spotify_matching.py::test_reviewed_replacement_edition_requires_new_confirmation`
379. `audioshelf/tests/test_spotify_matching.py::test_spotify_album_title_only_search_recovers_split_artist_credits`
380. `audioshelf/tests/test_spotify_matching.py::test_unresolved_album_stays_on_shelf_without_repopulating_unverified_tracks`
381. `audioshelf/tests/test_spotify_matching.py::test_unresolved_report_shows_spotify_album_and_musicbrainz_version_evidence`
382. `audioshelf/tests/test_spotify_matching.py::test_verified_bulk_match_saves_tracks_but_does_not_review_album`
383. `audioshelf/tests/test_track_artist_credits.py::test_artist_safeguard_stays_strict_for_uncredited_and_wrong_credit`
384. `audioshelf/tests/test_track_artist_credits.py::test_complete_soundtrack_matches_without_special_cases`
385. `audioshelf/tests/test_track_artist_credits.py::test_musicbrainz_track_performers_are_extracted_from_each_medium`
386. `audioshelf/tests/test_track_artist_credits.py::test_performer_credits_are_general_for_any_collaborative_album`
387. `audioshelf/tests/test_track_artist_credits.py::test_selecting_soundtrack_maps_every_track_and_keeps_review`
388. `audioshelf/tests/test_track_artist_credits.py::test_store_migration_keeps_shelf_and_hand_mappings`
389. `audioshelf/tests/test_tracklist_variants.py::test_browse_only_shows_spotify_verified_grouped_tracklists`
390. `audioshelf/tests/test_tracklist_variants.py::test_edition_picker_only_counts_gb_worldwide_and_us_releases`
391. `audioshelf/tests/test_tracklist_variants.py::test_equivalent_layout_tries_later_pressing_if_first_cannot_match`
392. `audioshelf/tests/test_tracklist_variants.py::test_equivalent_pressings_collapse_despite_remaster_and_recording_ids`
393. `audioshelf/tests/test_tracklist_variants.py::test_invalid_tracklist_page_is_rejected`
394. `audioshelf/tests/test_tracklist_variants.py::test_long_unplayable_editions_do_not_trigger_many_recording_requests`
395. `audioshelf/tests/test_tracklist_variants.py::test_matching_fallback_when_structured_search_has_no_complete_album`
396. `audioshelf/tests/test_tracklist_variants.py::test_multidisc_layout_kept_independent_of_same_song_sequence`
397. `audioshelf/tests/test_tracklist_variants.py::test_scanning_continues_across_all_eligible_pages`
398. `audioshelf/tests/test_tracklist_variants.py::test_select_vinyl_sides_preserves_disc_and_canonical_mapping`
399. `audioshelf/tests/test_tracklist_variants.py::test_side_choice_validation_and_stored_album_intact_on_failure`
400. `audioshelf/tests/test_tracklist_variants.py::test_successful_page_cached_for_retry_without_refetching`
401. `audioshelf/tests/test_tracklist_variants.py::test_unconnected_account_cannot_see_unverified_tracklists`
402. `audioshelf/tests/test_tracklist_variants.py::test_unknown_or_inconsistent_vinyl_numbers_do_not_create_fictitious_sides`
403. `audioshelf/tests/test_tracklist_variants.py::test_unmatched_album_has_reasons_and_no_selectable_tracklists`
404. `audioshelf/tests/test_tracklist_variants.py::test_unverified_disc_layout_not_offered_even_with_same_song_sequence`
405. `audioshelf/tests/test_vinyl.py::test_interface_persists_and_invalid_choice_is_atomic`
406. `audioshelf/tests/test_vinyl.py::test_playback_does_not_misidentify_shared_or_uncollected_tracks`
407. `audioshelf/tests/test_vinyl.py::test_playback_tracks_spotify_pause_and_relinked_library_track`
408. `audioshelf/tests/test_vinyl.py::test_shelf_furniture_persists_and_rejects_invalid_choices`

## Other release checks

1. Browser flows: `tests/test_ui.cjs`, `tests/test_vinyl_ui.cjs` and `tests/test_accounts_ui.cjs`.
2. Python compilation and launcher shell syntax.
3. Browser JavaScript syntax.
4. Add-on container build.
5. Test inventory freshness, including changed test bodies and CI configuration.

CI verifies completeness and freshness, not whether a human judgement about redundancy is correct. Review changed tests for duplicate coverage, obsolete requirements and meaningful regressions before updating this list. Git history retains previous numbered lists, timestamps and review notes.
