# AudioShelf test inventory

Reviewed: 2026-10-03T13:08:01+01:00 (Europe/London).

Reviewed add-on recovery coverage: password and Spotify preservation, invalidation of all existing access, changed-value one-time processing across clearing and re-enrolment, blank values and malformed options cover distinct recovery risks. Kept ingress and single-use recovery-code checks because they exercise separate recovery routes. Retained browser update tests from 0.4.2; no feature-removal or duplicate tests added.

<!-- inventory: {"reviewed_at": "2026-10-03T13:08:01+01:00", "review_note": "Reviewed add-on recovery coverage: password and Spotify preservation, invalidation of all existing access, changed-value one-time processing across clearing and re-enrolment, blank values and malformed options cover distinct recovery risks. Kept ingress and single-use recovery-code checks because they exercise separate recovery routes. Retained browser update tests from 0.4.2; no feature-removal or duplicate tests added.", "source_sha256": "a081fe77e4121e416aaa712037acecb8ebfe4db82ab1e1f18d4994d302e7648c"} -->

## Backend cases (223)

1. `audioshelf/tests/test_artwork.py::test_album_cover_then_edition_then_spotify_fallback`
2. `audioshelf/tests/test_artwork.py::test_artwork_requires_password`
3. `audioshelf/tests/test_artwork.py::test_cache_can_be_rebuilt_without_losing_collection`
4. `audioshelf/tests/test_artwork.py::test_cache_cannot_live_inside_collection`
5. `audioshelf/tests/test_artwork.py::test_cover_preview_uses_fixed_source_and_never_caches[False]`
6. `audioshelf/tests/test_artwork.py::test_cover_preview_uses_fixed_source_and_never_caches[True]`
7. `audioshelf/tests/test_artwork.py::test_database_backup_excludes_metadata_cache`
8. `audioshelf/tests/test_artwork.py::test_default_album_cover_ignores_tracklist_edition_and_refreshes_old_cache`
9. `audioshelf/tests/test_artwork.py::test_download_hosts_and_size_are_restricted`
10. `audioshelf/tests/test_artwork.py::test_downloaded_artwork_is_cached_outside_collection`
11. `audioshelf/tests/test_artwork.py::test_explicit_cover_precedes_album_default_without_library_cache`
12. `audioshelf/tests/test_artwork.py::test_invalid_artwork_upload_leaves_previous_cover`
13. `audioshelf/tests/test_artwork.py::test_invalid_preview_identifier_never_reaches_downloader`
14. `audioshelf/tests/test_artwork.py::test_missing_cover_retries_after_spotify_connects`
15. `audioshelf/tests/test_artwork.py::test_removal_evicts_download_and_keeps_uploaded_cover`
16. `audioshelf/tests/test_artwork.py::test_schema_upgrade_removes_old_reproducible_cache`
17. `audioshelf/tests/test_artwork.py::test_selected_cover_is_cached_only_after_album_joins_shelf`
18. `audioshelf/tests/test_artwork.py::test_startup_purges_store_only_artwork`
19. `audioshelf/tests/test_artwork.py::test_store_browsing_never_saves_artwork`
20. `audioshelf/tests/test_artwork.py::test_uploaded_cover_survives_restart_and_reset`
21. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[amber]`
22. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[forest]`
23. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[high-contrast]`
24. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[midnight]`
25. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[monochrome]`
26. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[ocean]`
27. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[paper]`
28. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[plum]`
29. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[record-store]`
30. `audioshelf/tests/test_catalogue_diagnostics.py::test_all_ten_themes_persist_without_changing_album[sunset]`
31. `audioshelf/tests/test_catalogue_diagnostics.py::test_amnesiac_alias_cannot_accept_kid_a_morning_bell_or_another_artist`
32. `audioshelf/tests/test_catalogue_diagnostics.py::test_amnesiac_printed_title_variants_verify_with_artist_and_duration[Dollars & Cents-Dollars and Cents-291000]`
33. `audioshelf/tests/test_catalogue_diagnostics.py::test_amnesiac_printed_title_variants_verify_with_artist_and_duration[Pull Pulk Revolving Doors-Pulk/Pull Revolving Doors-247000]`
34. `audioshelf/tests/test_catalogue_diagnostics.py::test_amnesiac_printed_title_variants_verify_with_artist_and_duration[The Morning Bell Amnesiac-Morning Bell/Amnesiac-194000]`
35. `audioshelf/tests/test_catalogue_diagnostics.py::test_beatles_core_catalogue_includes_soundtracks_excludes_regional_and_compilation`
36. `audioshelf/tests/test_catalogue_diagnostics.py::test_cover_selection_preserves_tracks_and_custom_cover_when_download_fails`
37. `audioshelf/tests/test_catalogue_diagnostics.py::test_dated_remix_is_rejected_while_stereo_mix_is_accepted`
38. `audioshelf/tests/test_catalogue_diagnostics.py::test_diagnostics_are_bounded_and_keep_only_latest_large_assessment`
39. `audioshelf/tests/test_catalogue_diagnostics.py::test_diagnostics_record_matching_decisions_and_errors_without_credentials`
40. `audioshelf/tests/test_catalogue_diagnostics.py::test_invalid_theme_does_not_partially_save_preferences`
41. `audioshelf/tests/test_catalogue_diagnostics.py::test_legacy_cassette_cover_uses_allowed_edition_without_changing_tracklist`
42. `audioshelf/tests/test_catalogue_diagnostics.py::test_non_album_series_cannot_be_selected`
43. `audioshelf/tests/test_catalogue_diagnostics.py::test_series_and_individual_overrides_work_for_other_artists_and_keep_shelf`
44. `audioshelf/tests/test_catalogue_diagnostics.py::test_successful_series_snapshot_survives_outage_for_any_artist`
45. `audioshelf/tests/test_catalogue_storage.py::test_backup_is_valid_and_excludes_pending_authorization`
46. `audioshelf/tests/test_catalogue_storage.py::test_changing_canonical_edition_clears_stale_mappings`
47. `audioshelf/tests/test_catalogue_storage.py::test_collaborative_album_belongs_to_both_credited_artists`
48. `audioshelf/tests/test_catalogue_storage.py::test_default_country_order_and_standard_edition_preference`
49. `audioshelf/tests/test_catalogue_storage.py::test_discography_excludes_minor_and_nonstudio_releases[Compilation]`
50. `audioshelf/tests/test_catalogue_storage.py::test_discography_excludes_minor_and_nonstudio_releases[DJ-mix]`
51. `audioshelf/tests/test_catalogue_storage.py::test_discography_excludes_minor_and_nonstudio_releases[Demo]`
52. `audioshelf/tests/test_catalogue_storage.py::test_discography_excludes_minor_and_nonstudio_releases[Live]`
53. `audioshelf/tests/test_catalogue_storage.py::test_discography_excludes_minor_and_nonstudio_releases[Remix]`
54. `audioshelf/tests/test_catalogue_storage.py::test_discography_excludes_minor_and_nonstudio_releases[Soundtrack]`
55. `audioshelf/tests/test_catalogue_storage.py::test_future_schema_does_not_get_downgraded`
56. `audioshelf/tests/test_catalogue_storage.py::test_manual_mapping_survives_automatic_reresolution`
57. `audioshelf/tests/test_catalogue_storage.py::test_original_two_disc_album_retains_both_discs`
58. `audioshelf/tests/test_catalogue_storage.py::test_shelf_persists_across_restart_and_remove_retains_mapping`
59. `audioshelf/tests/test_catalogue_storage.py::test_unresolved_manual_edition_can_be_fixed_by_automatic_matching`
60. `audioshelf/tests/test_generic_catalogues.py::test_catalogue_decisions_depend_on_membership_and_types_not_names[A film composer-Original score]`
61. `audioshelf/tests/test_generic_catalogues.py::test_catalogue_decisions_depend_on_membership_and_types_not_names[A new band-Second album]`
62. `audioshelf/tests/test_generic_catalogues.py::test_catalogue_decisions_depend_on_membership_and_types_not_names[An unfamiliar artist-Regional LP]`
63. `audioshelf/tests/test_generic_catalogues.py::test_catalogue_discovery_uses_artist_name_and_returns_only_release_group_series`
64. `audioshelf/tests/test_generic_catalogues.py::test_clearing_series_restores_generic_rules_and_keeps_shelf`
65. `audioshelf/tests/test_generic_catalogues.py::test_collaborative_catalogue_rules_do_not_depend_on_credit_order`
66. `audioshelf/tests/test_generic_catalogues.py::test_curated_rules_handle_soundtracks_for_every_artist[secondary0-True]`
67. `audioshelf/tests/test_generic_catalogues.py::test_curated_rules_handle_soundtracks_for_every_artist[secondary1-True]`
68. `audioshelf/tests/test_generic_catalogues.py::test_curated_rules_handle_soundtracks_for_every_artist[secondary2-False]`
69. `audioshelf/tests/test_generic_catalogues.py::test_curated_rules_handle_soundtracks_for_every_artist[secondary3-False]`
70. `audioshelf/tests/test_generic_catalogues.py::test_curated_rules_handle_soundtracks_for_every_artist[secondary4-False]`
71. `audioshelf/tests/test_generic_catalogues.py::test_explicit_catalogue_and_country_choices_survive_upgrade_and_restart`
72. `audioshelf/tests/test_generic_catalogues.py::test_no_artist_or_album_has_a_built_in_catalogue_exception`
73. `audioshelf/tests/test_generic_catalogues.py::test_old_cached_series_members_gain_a_persistent_snapshot`
74. `audioshelf/tests/test_generic_catalogues.py::test_series_snapshot_survives_restart_and_deleted_replaceable_cache`
75. `audioshelf/tests/test_generic_catalogues.py::test_unrelated_series_cannot_replace_selected_catalogue`
76. `audioshelf/tests/test_matching.py::test_alternative_recordings_not_silently_accepted[Opening (Demo)]`
77. `audioshelf/tests/test_matching.py::test_alternative_recordings_not_silently_accepted[Opening - Acoustic]`
78. `audioshelf/tests/test_matching.py::test_alternative_recordings_not_silently_accepted[Opening - Instrumental]`
79. `audioshelf/tests/test_matching.py::test_alternative_recordings_not_silently_accepted[Opening - Live]`
80. `audioshelf/tests/test_matching.py::test_alternative_recordings_not_silently_accepted[Opening - Radio Edit]`
81. `audioshelf/tests/test_matching.py::test_alternative_recordings_not_silently_accepted[Opening - Remix]`
82. `audioshelf/tests/test_matching.py::test_bonus_track_remaster_year_does_not_change_album_preference`
83. `audioshelf/tests/test_matching.py::test_clean_standard_edition_wins_equal_matching`
84. `audioshelf/tests/test_matching.py::test_dated_studio_mix_matches_but_dance_remix_still_rejected`
85. `audioshelf/tests/test_matching.py::test_deluxe_bonus_tracks_are_never_mapped`
86. `audioshelf/tests/test_matching.py::test_duplicate_titles_use_distinct_ordered_recordings`
87. `audioshelf/tests/test_matching.py::test_incomplete_recent_remaster_does_not_beat_complete_older_edition`
88. `audioshelf/tests/test_matching.py::test_latest_labelled_remaster_wins_even_with_bonus_tracks`
89. `audioshelf/tests/test_matching.py::test_missing_track_is_not_replaced_by_a_bonus_track`
90. `audioshelf/tests/test_matching.py::test_normalization_keeps_meaningful_version_labels`
91. `audioshelf/tests/test_matching.py::test_recent_reissue_date_does_not_prove_new_remaster`
92. `audioshelf/tests/test_matching.py::test_same_title_with_different_duration_needs_review`
93. `audioshelf/tests/test_matching.py::test_wrong_artist_and_unavailable_tracks_are_rejected`
94. `audioshelf/tests/test_musicbrainz_transport.py::test_artist_discography_paginates_filters_and_sorts`
95. `audioshelf/tests/test_musicbrainz_transport.py::test_first_tracklist_selection_and_group_validation`
96. `audioshelf/tests/test_musicbrainz_transport.py::test_lucene_special_characters_are_literal`
97. `audioshelf/tests/test_musicbrainz_transport.py::test_musicbrainz_requests_cache_and_wait_between_calls`
98. `audioshelf/tests/test_musicbrainz_transport.py::test_release_pagination_increments_actual_page_size`
99. `audioshelf/tests/test_recording_metadata.py::test_any_artist_can_match_the_linked_recording_names[Other spelling - 2022 Mix]`
100. `audioshelf/tests/test_recording_metadata.py::test_any_artist_can_match_the_linked_recording_names[Other spelling]`
101. `audioshelf/tests/test_recording_metadata.py::test_any_artist_can_match_the_linked_recording_names[Recording title]`
102. `audioshelf/tests/test_recording_metadata.py::test_existing_shelf_enriches_unmatched_tracks_and_preserves_manual_corrections[False]`
103. `audioshelf/tests/test_recording_metadata.py::test_existing_shelf_enriches_unmatched_tracks_and_preserves_manual_corrections[True]`
104. `audioshelf/tests/test_recording_metadata.py::test_metadata_outage_keeps_saved_names_and_mappings`
105. `audioshelf/tests/test_recording_metadata.py::test_no_radiohead_specific_exception_remains`
106. `audioshelf/tests/test_recording_metadata.py::test_real_version_three_migration_preserves_collection_and_manual_mapping`
107. `audioshelf/tests/test_recording_metadata.py::test_recording_aliases_keep_identity_duration_and_version_safeguards[changes0-Other spelling]`
108. `audioshelf/tests/test_recording_metadata.py::test_recording_aliases_keep_identity_duration_and_version_safeguards[changes1-Other spelling]`
109. `audioshelf/tests/test_recording_metadata.py::test_recording_aliases_keep_identity_duration_and_version_safeguards[changes2-Other spelling]`
110. `audioshelf/tests/test_recording_metadata.py::test_recording_aliases_keep_identity_duration_and_version_safeguards[changes3-Other spelling]`
111. `audioshelf/tests/test_recording_metadata.py::test_recording_aliases_keep_identity_duration_and_version_safeguards[changes4-Other spelling]`
112. `audioshelf/tests/test_recording_metadata.py::test_recording_aliases_keep_identity_duration_and_version_safeguards[changes5-Other spelling - Live]`
113. `audioshelf/tests/test_recording_metadata.py::test_recording_aliases_keep_identity_duration_and_version_safeguards[changes6-Other spelling - Demo]`
114. `audioshelf/tests/test_recording_metadata.py::test_recording_aliases_keep_identity_duration_and_version_safeguards[changes7-Other spelling - 2022 Remix]`
115. `audioshelf/tests/test_recording_metadata.py::test_recording_metadata_survives_save_and_restart`
116. `audioshelf/tests/test_recording_metadata.py::test_verified_manual_track_does_not_trigger_recording_lookup`
117. `audioshelf/tests/test_recording_metadata.py::test_wrong_recording_response_cannot_supply_aliases`
118. `audioshelf/tests/test_release_filters.py::test_album_country_preference_does_not_change_other_albums`
119. `audioshelf/tests/test_release_filters.py::test_any_country_can_be_preferred_without_hidden_country_penalties[AU]`
120. `audioshelf/tests/test_release_filters.py::test_any_country_can_be_preferred_without_hidden_country_penalties[JP]`
121. `audioshelf/tests/test_release_filters.py::test_any_country_can_be_preferred_without_hidden_country_penalties[US]`
122. `audioshelf/tests/test_release_filters.py::test_browse_and_automatic_selection_skip_early_cassette`
123. `audioshelf/tests/test_release_filters.py::test_country_and_format_priorities_with_original_reissue_safeguard`
124. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats0-True]`
125. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats1-True]`
126. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats10-False]`
127. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats11-False]`
128. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats12-False]`
129. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats13-False]`
130. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats2-True]`
131. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats3-True]`
132. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats4-True]`
133. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats5-True]`
134. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats6-True]`
135. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats7-True]`
136. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats8-False]`
137. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[GB-formats9-False]`
138. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[None-formats17-True]`
139. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[US-formats14-True]`
140. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[XE-formats16-True]`
141. `audioshelf/tests/test_release_filters.py::test_default_countries_and_all_audio_media[XW-formats15-True]`
142. `audioshelf/tests/test_release_filters.py::test_direct_selection_cannot_bypass_filters_or_clear_mappings`
143. `audioshelf/tests/test_release_filters.py::test_filter_change_applies_to_cached_raw_pages`
144. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[None]`
145. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[value1]`
146. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[value2]`
147. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[value3]`
148. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[value4]`
149. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[value5]`
150. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[value6]`
151. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[value7]`
152. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[value8]`
153. `audioshelf/tests/test_release_filters.py::test_invalid_settings_leave_preferences_unchanged[value9]`
154. `audioshelf/tests/test_release_filters.py::test_multiple_release_events_include_gb_but_missing_area_does_not`
155. `audioshelf/tests/test_release_filters.py::test_no_country_preference_uses_date_and_format_equally_for_all_countries`
156. `audioshelf/tests/test_release_filters.py::test_no_matching_editions_has_settings_guidance_without_fallback`
157. `audioshelf/tests/test_release_filters.py::test_paginated_picker_filters_each_page_and_keeps_raw_cursor`
158. `audioshelf/tests/test_release_filters.py::test_preferences_persist_preserve_mappings_and_are_backed_up`
159. `audioshelf/tests/test_release_filters.py::test_settings_mutations_require_application_header`
160. `audioshelf/tests/test_release_filters.py::test_strict_country_setting_is_optional_and_validated`
161. `audioshelf/tests/test_release_filters.py::test_upgrade_from_version_two_keeps_collection_and_sets_defaults`
162. `audioshelf/tests/test_routes.py::test_callback_bad_state_does_not_connect`
163. `audioshelf/tests/test_routes.py::test_canonical_change_requires_confirmation`
164. `audioshelf/tests/test_routes.py::test_export_contains_library_without_tokens_or_oauth`
165. `audioshelf/tests/test_routes.py::test_ingress_assets_and_urls_are_prefixed`
166. `audioshelf/tests/test_routes.py::test_invalid_ids_rejected_without_remote_request`
167. `audioshelf/tests/test_routes.py::test_mutations_require_same_origin_custom_header`
168. `audioshelf/tests/test_routes.py::test_password_protection_and_spoofed_ingress_header`
169. `audioshelf/tests/test_routes.py::test_preferred_device_settings_validation_and_clear`
170. `audioshelf/tests/test_routes.py::test_shell_assets_share_content_version_and_update_worker_is_not_cached`
171. `audioshelf/tests/test_routes.py::test_shell_health_and_empty_shelf`
172. `audioshelf/tests/test_security.py::test_blank_password_locks_standalone_but_ingress_still_works`
173. `audioshelf/tests/test_security.py::test_blank_reset_option_does_not_disable_enrolled_factor`
174. `audioshelf/tests/test_security.py::test_cross_origin_mutation_is_rejected`
175. `audioshelf/tests/test_security.py::test_ha_option_recovers_lost_factor_and_preserves_password_spotify_and_collection`
176. `audioshelf/tests/test_security.py::test_ha_reset_is_once_per_changed_value_even_after_clearing_and_reenrolling`
177. `audioshelf/tests/test_security.py::test_home_assistant_can_recover_totp_without_factor_but_spoofed_header_cannot`
178. `audioshelf/tests/test_security.py::test_login_throttle_survives_restart_and_ignores_forwarded_ip`
179. `audioshelf/tests/test_security.py::test_password_change_invalidates_existing_sessions`
180. `audioshelf/tests/test_security.py::test_reset_option_rejects_invalid_configuration_without_disabling_factor[123]`
181. `audioshelf/tests/test_security.py::test_reset_option_rejects_invalid_configuration_without_disabling_factor[True]`
182. `audioshelf/tests/test_security.py::test_reset_option_rejects_invalid_configuration_without_disabling_factor[xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx]`
183. `audioshelf/tests/test_security.py::test_security_changes_require_fresh_owner_factor_even_on_trusted_browser`
184. `audioshelf/tests/test_security.py::test_sessions_and_factor_setup_survive_restarts_and_setup_expires`
185. `audioshelf/tests/test_security.py::test_setup_bruteforce_and_expiry_are_limited`
186. `audioshelf/tests/test_security.py::test_standard_sessions_are_secure_expire_and_logout_revokes`
187. `audioshelf/tests/test_security.py::test_support_disabled_by_default_and_secrets_not_in_collection`
188. `audioshelf/tests/test_security.py::test_support_master_toggle_revokes_existing_and_prevents_resurrection`
189. `audioshelf/tests/test_security.py::test_support_permissions_expiry_and_revocation_are_server_enforced[control]`
190. `audioshelf/tests/test_security.py::test_support_permissions_expiry_and_revocation_are_server_enforced[view]`
191. `audioshelf/tests/test_security.py::test_totp_matches_rfc_vector`
192. `audioshelf/tests/test_security.py::test_totp_requires_factor_rejects_replay_and_recovery_is_single_use`
193. `audioshelf/tests/test_security.py::test_trusted_browser_credential_is_revoked_on_logout`
194. `audioshelf/tests/test_security.py::test_trusted_browser_still_needs_password_and_is_revocable`
195. `audioshelf/tests/test_spotify_auth_playback.py::test_album_tracks_pagination_is_complete`
196. `audioshelf/tests/test_spotify_auth_playback.py::test_api_401_refreshes_once`
197. `audioshelf/tests/test_spotify_auth_playback.py::test_candidate_search_reaches_new_remaster_on_next_page`
198. `audioshelf/tests/test_spotify_auth_playback.py::test_disc_playback_excludes_other_discs_and_allows_unmapped_other_disc`
199. `audioshelf/tests/test_spotify_auth_playback.py::test_expired_oauth_is_consumed_without_exchange`
200. `audioshelf/tests/test_spotify_auth_playback.py::test_fallback_candidate_search_paginates_when_structured_search_is_empty`
201. `audioshelf/tests/test_spotify_auth_playback.py::test_invalid_disc_never_starts_playback[-1]`
202. `audioshelf/tests/test_spotify_auth_playback.py::test_invalid_disc_never_starts_playback[0]`
203. `audioshelf/tests/test_spotify_auth_playback.py::test_invalid_disc_never_starts_playback[1.5]`
204. `audioshelf/tests/test_spotify_auth_playback.py::test_invalid_disc_never_starts_playback[1]`
205. `audioshelf/tests/test_spotify_auth_playback.py::test_invalid_disc_never_starts_playback[3]`
206. `audioshelf/tests/test_spotify_auth_playback.py::test_invalid_disc_never_starts_playback[True]`
207. `audioshelf/tests/test_spotify_auth_playback.py::test_no_active_device_has_actionable_error`
208. `audioshelf/tests/test_spotify_auth_playback.py::test_partial_mapping_cannot_play`
209. `audioshelf/tests/test_spotify_auth_playback.py::test_pkce_and_single_use_oauth_state`
210. `audioshelf/tests/test_spotify_auth_playback.py::test_playback_sends_only_exact_track_uris_and_turns_off_shuffle_repeat`
211. `audioshelf/tests/test_spotify_auth_playback.py::test_preferred_device_transfer_preserves_exact_disc_queue`
212. `audioshelf/tests/test_spotify_auth_playback.py::test_preferred_device_transfer_timeout_never_starts_tracks`
213. `audioshelf/tests/test_spotify_auth_playback.py::test_refresh_preserves_old_refresh_token_and_survives_restart`
214. `audioshelf/tests/test_spotify_auth_playback.py::test_shuffle_not_acknowledged_does_not_start_album`
215. `audioshelf/tests/test_spotify_auth_playback.py::test_spotify_album_link_formats[aaaaaaaaaaaaaaaaaaaaaa]`
216. `audioshelf/tests/test_spotify_auth_playback.py::test_spotify_album_link_formats[https://open.spotify.com/album/aaaaaaaaaaaaaaaaaaaaaa?si=hello]`
217. `audioshelf/tests/test_spotify_auth_playback.py::test_spotify_album_link_formats[https://open.spotify.com/intl-de/album/aaaaaaaaaaaaaaaaaaaaaa]`
218. `audioshelf/tests/test_spotify_auth_playback.py::test_spotify_album_link_formats[spotify:album:aaaaaaaaaaaaaaaaaaaaaa]`
219. `audioshelf/tests/test_spotify_auth_playback.py::test_unavailable_or_ambiguous_preference_never_plays_elsewhere[devices0]`
220. `audioshelf/tests/test_spotify_auth_playback.py::test_unavailable_or_ambiguous_preference_never_plays_elsewhere[devices1]`
221. `audioshelf/tests/test_spotify_auth_playback.py::test_unavailable_or_ambiguous_preference_never_plays_elsewhere[devices2]`
222. `audioshelf/tests/test_spotify_auth_playback.py::test_unreviewed_tracklist_cannot_play`
223. `audioshelf/tests/test_spotify_auth_playback.py::test_untrusted_spotify_urls_not_fetched`

## Other release checks

1. Browser flows: `tests/test_ui.cjs`.
2. Python compilation and launcher shell syntax.
3. Browser JavaScript syntax.
4. Add-on container build.
5. Test inventory freshness, including changed test bodies and CI configuration.

CI verifies completeness and freshness, not whether a human judgement about redundancy is correct. Review changed tests for duplicate coverage, obsolete requirements and meaningful regressions before updating this list. Git history retains previous numbered lists, timestamps and review notes.
