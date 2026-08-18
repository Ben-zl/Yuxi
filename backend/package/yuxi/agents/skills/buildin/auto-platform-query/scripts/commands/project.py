"""
项目配置查询命令
"""

import sys
import click
import automation_api
from automation_api import AutomationAPIError, NetworkError, APIException


@click.command('config')
@click.option('--email-template', is_flag=True, help='获取邮件模板')
@click.option('--email-config', is_flag=True, help='获取邮件配置')
@click.option('--xiezuo-token', is_flag=True, help='获取协同 Token')
@click.option('--trend-pipelines', is_flag=True, help='获取趋势流水线')
@click.option('--group-relations', is_flag=True, help='获取项目组关系')
@click.pass_context
def config_cmd(ctx, email_template, email_config, xiezuo_token,
               trend_pipelines, group_relations):
    """
    查询项目配置信息

    示例:
        # 获取项目配置
        python cli.py config

        # 获取邮件模板
        python cli.py config --email-template

        # 获取协同 Token
        python cli.py config --xiezuo-token
    """

    output_file = ctx.obj['output_file']
    verbose = ctx.obj['verbose']

    try:
        from formatters import get_formatter
        formatter = get_formatter()
        from utils.output import print_output, print_error

        if email_template:
            # 获取邮件模板
            if verbose:
                click.echo("正在获取邮件模板...")
            data = automation_api.get_email_template()
            output = formatter.format_config(data)

        elif email_config:
            # 获取邮件配置
            if verbose:
                click.echo("正在获取邮件配置...")
            data = automation_api.get_email_config()
            output = formatter.format_config(data)

        elif xiezuo_token:
            # 获取协同 Token
            if verbose:
                click.echo("正在获取协同 Token...")
            data = automation_api.get_xiezuo_token()
            output = formatter.format_config(data)

        elif trend_pipelines:
            # 获取趋势流水线
            if verbose:
                click.echo("正在获取趋势流水线...")
            data = automation_api.get_trend_pipelines()
            output = formatter.format_pipelines(data)

        elif group_relations:
            # 获取项目组关系
            if verbose:
                click.echo("正在获取项目组关系...")
            data = automation_api.get_project_group_relations()
            output = formatter.format_config(data)

        else:
            # 获取项目配置
            if verbose:
                click.echo("正在获取项目配置...")
            data = automation_api.get_project_config()
            output = formatter.format_config(data)

        # 输出结果
        print_output(output, output_file)

    except NetworkError as e:
        print_error(f"网络错误: {e}")
        sys.exit(1)
    except APIException as e:
        print_error(f"API 错误: {e}")
        sys.exit(1)
    except AutomationAPIError as e:
        print_error(f"错误: {e}")
        sys.exit(1)
    except Exception as e:
        if verbose:
            import traceback
            traceback.print_exc()
        print_error(f"未知错误: {e}")
        sys.exit(1)
