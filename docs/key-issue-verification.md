# 关键地方议题核验

`runtime.key_issue_verifier` 消费现有自动研究模块已经完成正文读取和逐字引用校验的 findings。

进入 current_issue 的最低条件是：同一县市议题至少有两个不同 `publisher_id` 的 C 级正文来源，并且正式晋升前显式确认已补查主管机关更新、反对意见、否认或更正。未完成反证检查时，即使两家媒体内容一致，也会被既有 KnowledgePromotionBuilder 拒绝。

该记录只表示两个独立来源在指定时间点均报道该议题，不把报道数量转换为民意、支持度或结果判断。
