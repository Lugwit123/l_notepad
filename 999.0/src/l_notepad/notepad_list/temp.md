
`NodeDetailDialog` 的 `_detect_kind` 已经覆盖了全部 5 种节点类型。问题是——右键菜单的 `_show_tree_node_details` 是怎么路由的？可能只有 FileNode 真正走到了这个弹窗。
`_show_tree_node_details` **实际上已经通用了**——L16568-L16577 对全部 5 种节点类型都做了分支，L16645 统一走 `NodeDetailDialog`。

但让我确认右键菜单里是不是对所有类型都显示了"查看节点详情"：
