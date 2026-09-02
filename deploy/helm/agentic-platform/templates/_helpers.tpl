{{- define "agentic-platform.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "agentic-platform.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "agentic-platform.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "agentic-platform.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}{{ default (include "agentic-platform.fullname" .) .Values.serviceAccount.name }}{{ else }}{{ required "serviceAccount.name is required when create=false" .Values.serviceAccount.name }}{{ end }}
{{- end }}
