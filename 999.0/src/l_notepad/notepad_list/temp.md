[2026-06-03 18:09:41] {WARNING}
[arg0] "[DEBUG] _ask_refresh_mismatch_asset_attachment_options 返回值: {'refresh_asset_details': True, 'only_missing_attachments': Fal
se, 'only_missing_archive_metadata': False, 'download_missing_or_damaged_archives': True, 'redownload_complete_split_archives': False,
 'redownload_complete_normal_archives': False, 'validate_downloaded_archives': True, 'refresh_7z_metadata': True, 'extract_and_compute
_md5': True, 'preserve_old_metadata_on_failure': True, 'write_scan_cache': True, 'update_ui_after_finish': True, 'recompute_mismatch_a
fter_finish': True, 'detailed_logs': True}"   ----code_context : jobs_before_filter = len(jobs)
File: c:\users\wb.fengqingqing\packages\anim_upload_muse_tool\999.0.0\src\anim_upload_muse_tool\j_disc_backtomuse\j_disc_backup_ui.py:
12952, -fn: on_refresh_mismatch_asset_attachments_clicked, 打印次数: 1/4


[2026-06-03 18:10:34] {WARNING}

[arg0] "[7z元数据][分卷预热汇总]"

[arg1] ["扫描资产数", 4082]

[arg2] ["分卷资产数", 21]

[arg3] ["待统计资产数", 21]

[arg4] ["并行数", 5]

[arg5] ["新增元数据资产数", 0]

[arg6] ["仍缺元数据资产数", 21]   ----code_context : lprint(

                "[7z元数据][分卷预热汇总]",

                ("扫描资产数", scanned_assets),

                ("分卷资产数", split_assets),

                ("待统计资产数", len(candidate_assets)),

                ("并行数", worker_count),

                ("新增元数据资产数", updated_assets),

                ("仍缺元数据资产数", missing_metadata_assets),

            )

File: c:\users\wb.fengqingqing\packages\anim_upload_muse_tool\999.0.0\src\anim_upload_muse_tool\j_disc_backtomuse\backup_ui_helper.py:

1543, -fn: _ensure_split_volume_archive_metadata_for_folder_tree, 打印次数: 2/4

刷新不匹配资产附件缓存按钮点击后,找到了21个分卷资产,总是不进入解压阶段,验证模式和备份模式对压缩包的处理是一样的

