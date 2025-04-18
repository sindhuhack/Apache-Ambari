"""
Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

Ambari Agent

"""

# Python Imports
import os

# Ambari Common and Resource Management Imports
from resource_management.libraries.script.script import Script
from resource_management.core.resources.service import ServiceConfig
from resource_management.libraries.functions.default import default
from resource_management.libraries.functions.format import format
from resource_management.libraries.functions.generate_logfeeder_input_config import generate_logfeeder_input_config
from resource_management.libraries.functions.is_empty import is_empty
from resource_management.libraries.functions.lzo_utils import install_lzo_if_needed
from resource_management.core.resources.system import Directory, Execute
from resource_management.core.resources.system import File
from resource_management.libraries.resources.xml_config import XmlConfig
from resource_management.core.source import InlineTemplate, Template
from ambari_commons.os_family_impl import OsFamilyFuncImpl, OsFamilyImpl
from ambari_commons import OSConst

from resource_management.libraries.functions.mounted_dirs_helper import handle_mounted_dirs
from hbase_service import create_hbase_package, copy_hbase_package_to_hdfs


@OsFamilyFuncImpl(os_family=OsFamilyImpl.DEFAULT)
def yarn(name=None, config_dir=None):
  """
  :param name: Component name, apptimelinereader, apptimelineserver, nodemanager, resourcemanager, or None (defaults for client)
  :param config_dir: Which config directory to write configs to, which could be different during rolling upgrade.
  """
  import params

  install_lzo_if_needed()

  if config_dir is None:
    config_dir = params.hadoop_conf_dir

  Directory([params.yarn_log_dir_prefix],
            owner=params.yarn_user,
            group=params.user_group,
            create_parents=True,
            ignore_failures=True,
            cd_access='a',
            mode=0o775,
            )

  Directory([params.yarn_pid_dir_prefix, params.yarn_pid_dir, params.yarn_log_dir],
            owner=params.yarn_user,
            group=params.user_group,
            create_parents=True,
            cd_access='a',
  )

  Directory([params.mapred_pid_dir_prefix, params.mapred_pid_dir, params.mapred_log_dir_prefix, params.mapred_log_dir],
            owner=params.mapred_user,
            group=params.user_group,
            create_parents=True,
            cd_access='a',
  )

  Directory(params.yarn_hbase_conf_dir,
            owner = params.yarn_hbase_user,
            group = params.user_group,
            create_parents = True,
            cd_access='a',
            )

  # Some of these function calls depend on the directories above being created first.
  if name == 'resourcemanager':
    setup_resourcemanager()
  elif name == 'nodemanager':
    setup_nodemanager()
  elif name == 'apptimelineserver':
    setup_ats()
  elif name == 'historyserver':
    setup_historyserver()
  elif name == 'apptimelinereader':
    if not params.use_external_hbase and not params.is_hbase_system_service_launch:
       setup_atsv2_hbase_directories()
       setup_atsv2_hbase_files()

  generate_logfeeder_input_config('yarn', Template("input.config-yarn.json.j2", extra_imports=[default]))

   # if there is the viewFS mount table content, create separate xml config and include in in the core-site
   # else just create core-site
  if params.mount_table_content:
    XmlConfig("core-site.xml",
              conf_dir=config_dir,
              configurations=params.config['configurations']['core-site'],
              configuration_attributes=params.config['configurationAttributes']['core-site'],
              owner=params.hdfs_user,
              group=params.user_group,
              mode=0o644,
              xml_include_file=os.path.join(config_dir, params.xml_inclusion_file_name)
    )

    File(os.path.join(config_dir, params.xml_inclusion_file_name),
         owner=params.hdfs_user,
         group=params.user_group,
         content=params.mount_table_content,
         mode=0o644
    )
  else:
    XmlConfig("core-site.xml",
              conf_dir=config_dir,
              configurations=params.config['configurations']['core-site'],
              configuration_attributes=params.config['configurationAttributes']['core-site'],
              owner=params.hdfs_user,
              group=params.user_group,
              mode=0o644
    )

  # During RU, Core Masters and Slaves need hdfs-site.xml
  # TODO, instead of specifying individual configs, which is susceptible to breaking when new configs are added,
  # RU should rely on all available in <stack-root>/<version>/hadoop/conf
  XmlConfig("hdfs-site.xml",
            conf_dir=config_dir,
            configurations=params.config['configurations']['hdfs-site'],
            configuration_attributes=params.config['configurationAttributes']['hdfs-site'],
            owner=params.hdfs_user,
            group=params.user_group,
            mode=0o644
  )

  XmlConfig("mapred-site.xml",
            conf_dir=config_dir,
            configurations=params.config['configurations']['mapred-site'],
            configuration_attributes=params.config['configurationAttributes']['mapred-site'],
            owner=params.yarn_user,
            group=params.user_group,
            mode=0o644
  )

  configs = {}
  configs.update(params.config['configurations']['yarn-site'])
  configs["hadoop.registry.dns.bind-port"] = params.config['configurations']['yarn-env']['registry.dns.bind-port']
  XmlConfig("yarn-site.xml",
            conf_dir=config_dir,
            configurations=configs,
            configuration_attributes=params.config['configurationAttributes']['yarn-site'],
            owner=params.yarn_user,
            group=params.user_group,
            mode=0o644
  )

  XmlConfig("capacity-scheduler.xml",
            conf_dir=config_dir,
            configurations=params.config['configurations']['capacity-scheduler'],
            configuration_attributes=params.config['configurationAttributes']['capacity-scheduler'],
            owner=params.yarn_user,
            group=params.user_group,
            mode=0o644
  )

  XmlConfig("hbase-site.xml",
            conf_dir=params.yarn_hbase_conf_dir,
            configurations=params.config['configurations']['yarn-hbase-site'],
            configuration_attributes=params.config['configurationAttributes']['yarn-hbase-site'],
            owner=params.yarn_hbase_user,
            group=params.user_group,
            mode=0o644
  )

  XmlConfig("resource-types.xml",
            conf_dir=config_dir,
            configurations=params.config['configurations']['resource-types'],
            configuration_attributes=params.config['configurationAttributes']['resource-types'],
            owner=params.yarn_user,
            group=params.user_group,
            mode=0o644
  )

  File(format("{limits_conf_dir}/yarn.conf"),
       mode=0o644,
       content=Template('yarn.conf.j2')
  )

  File(format("{limits_conf_dir}/mapreduce.conf"),
       mode=0o644,
       content=Template('mapreduce.conf.j2')
  )

  File(os.path.join(config_dir, "yarn-env.sh"),
       owner=params.yarn_user,
       group=params.user_group,
       mode=0o755,
       content=InlineTemplate(params.yarn_env_sh_template)
  )

  File(format("{yarn_bin}/container-executor"),
      group=params.yarn_executor_container_group,
      mode=params.container_executor_mode
  )

  File(os.path.join(config_dir, "container-executor.cfg"),
      owner='root',
      group=params.user_group,
      mode=0o644,
      content=InlineTemplate(params.container_executor_cfg_template)
  )

  Directory(params.cgroups_dir,
            group=params.user_group,
            create_parents = True,
            mode=0o755,
            cd_access="a")

  File(os.path.join(config_dir, "mapred-env.sh"),
       owner=params.tc_owner,
       mode=0o755,
       content=InlineTemplate(params.mapred_env_sh_template)
  )

  if params.yarn_nodemanager_recovery_dir:
    Directory(InlineTemplate(params.yarn_nodemanager_recovery_dir).get_content(),
              owner=params.yarn_user,
              group=params.user_group,
              create_parents=True,
              mode=0o755,
              cd_access='a',
    )

  if params.security_enabled:
    File(os.path.join(params.hadoop_bin, "task-controller"),
         owner="root",
         group=params.mapred_tt_group,
         mode=0o6050
    )
    File(os.path.join(config_dir, 'taskcontroller.cfg'),
         owner = params.tc_owner,
         mode = params.tc_mode,
         group = params.mapred_tt_group,
         content=Template("taskcontroller.cfg.j2")
    )
    File(os.path.join(config_dir, 'yarn_jaas.conf'),
         owner=params.yarn_user,
         group=params.user_group,
         mode=0o644,
         content=Template("yarn_jaas.conf.j2")
    )
    if params.has_ats:
      File(os.path.join(config_dir, 'yarn_ats_jaas.conf'),
           owner=params.yarn_user,
           group=params.user_group,
           mode=0o644,
           content=Template("yarn_ats_jaas.conf.j2")
      )
    if params.has_registry_dns:
      File(os.path.join(config_dir, 'yarn_registry_dns_jaas.conf'),
           owner=params.yarn_user,
           group=params.user_group,
           mode=0o644,
           content=Template("yarn_registry_dns_jaas.conf.j2")
      )
    File(os.path.join(config_dir, 'yarn_nm_jaas.conf'),
         owner=params.yarn_user,
         group=params.user_group,
         mode=0o644,
         content=Template("yarn_nm_jaas.conf.j2")
    )
    if params.has_hs:
      File(os.path.join(config_dir, 'mapred_jaas.conf'),
           owner=params.mapred_user,
           group=params.user_group,
           mode=0o644,
           content=Template("mapred_jaas.conf.j2")
      )
  else:
    File(os.path.join(config_dir, 'taskcontroller.cfg'),
         owner=params.tc_owner,
         content=Template("taskcontroller.cfg.j2")
    )

  XmlConfig("mapred-site.xml",
            conf_dir=config_dir,
            configurations=params.config['configurations']['mapred-site'],
            configuration_attributes=params.config['configurationAttributes']['mapred-site'],
            mode=0o644,
            owner=params.mapred_user,
            group=params.user_group
  )

  XmlConfig("capacity-scheduler.xml",
            conf_dir=config_dir,
            configurations=params.config['configurations'][
              'capacity-scheduler'],
            configuration_attributes=params.config['configurationAttributes']['capacity-scheduler'],
            mode=0o644,
            owner=params.hdfs_user,
            group=params.user_group
  )

  if "ssl-client" in params.config['configurations']:
    XmlConfig("ssl-client.xml",
              conf_dir=config_dir,
              configurations=params.config['configurations']['ssl-client'],
              configuration_attributes=params.config['configurationAttributes']['ssl-client'],
              mode=0o644,
              owner=params.hdfs_user,
              group=params.user_group
    )

    Directory(params.hadoop_conf_secure_dir,
              create_parents = True,
              owner='root',
              group=params.user_group,
              cd_access='a',
              )

    XmlConfig("ssl-client.xml",
              conf_dir=params.hadoop_conf_secure_dir,
              configurations=params.config['configurations']['ssl-client'],
              configuration_attributes=params.config['configurationAttributes']['ssl-client'],
              mode=0o644,
              owner=params.hdfs_user,
              group=params.user_group
    )

  if "ssl-server" in params.config['configurations']:
    XmlConfig("ssl-server.xml",
              conf_dir=config_dir,
              configurations=params.config['configurations']['ssl-server'],
              configuration_attributes=params.config['configurationAttributes']['ssl-server'],
              mode=0o644,
              owner=params.hdfs_user,
              group=params.user_group
    )
  if os.path.exists(os.path.join(config_dir, 'fair-scheduler.xml')):
    File(os.path.join(config_dir, 'fair-scheduler.xml'),
         mode=0o644,
         owner=params.mapred_user,
         group=params.user_group
    )

  if os.path.exists(
    os.path.join(config_dir, 'ssl-client.xml.example')):
    File(os.path.join(config_dir, 'ssl-client.xml.example'),
         mode=0o644,
         owner=params.mapred_user,
         group=params.user_group
    )

  if os.path.exists(
    os.path.join(config_dir, 'ssl-server.xml.example')):
    File(os.path.join(config_dir, 'ssl-server.xml.example'),
         mode=0o644,
         owner=params.mapred_user,
         group=params.user_group
    )

  atsv2_host = default("/clusterHostInfo/timeline_reader_hosts", [])
  has_atsv2 = not len(atsv2_host) == 0
  if has_atsv2:
    setup_atsv2_backend(name,config_dir)

def setup_historyserver():
  import params

  if params.yarn_log_aggregation_enabled:
    params.HdfsResource(params.yarn_nm_app_log_dir,
                         action="create_on_execute",
                         type="directory",
                         owner=params.yarn_user,
                         group=params.user_group,
                         mode=0o1777,
                         recursive_chmod=True
    )

  # create the /tmp folder with proper permissions if it doesn't exist yet
  if params.entity_file_history_directory.startswith('/tmp'):
      params.HdfsResource(params.hdfs_tmp_dir,
                          action="create_on_execute",
                          type="directory",
                          owner=params.hdfs_user,
                          mode=0o777,
      )

  params.HdfsResource(params.entity_file_history_directory,
                         action="create_on_execute",
                         type="directory",
                         owner=params.yarn_user,
                         group=params.user_group
  )
  params.HdfsResource("/mapred",
                       type="directory",
                       action="create_on_execute",
                       owner=params.mapred_user
  )
  params.HdfsResource("/mapred/system",
                       type="directory",
                       action="create_on_execute",
                       owner=params.hdfs_user
  )
  params.HdfsResource(params.mapreduce_jobhistory_done_dir,
                       type="directory",
                       action="create_on_execute",
                       owner=params.mapred_user,
                       group=params.user_group,
                       change_permissions_for_parents=True,
                       mode=0o777
  )
  params.HdfsResource(None, action="execute")
  Directory(params.jhs_leveldb_state_store_dir,
            owner=params.mapred_user,
            group=params.user_group,
            create_parents = True,
            cd_access="a",
            recursive_ownership = True,
            )

  generate_logfeeder_input_config('mapreduce2', Template("input.config-mapreduce2.json.j2", extra_imports=[default]))

def setup_nodemanager():
  import params

  # First start after enabling/disabling security
  if params.toggle_nm_security:
    Directory(params.nm_local_dirs_list + params.nm_log_dirs_list,
              action='delete'
    )

    # If yarn.nodemanager.recovery.dir exists, remove this dir
    if params.yarn_nodemanager_recovery_dir:
      Directory(InlineTemplate(params.yarn_nodemanager_recovery_dir).get_content(),
                action='delete'
      )

    # Setting NM marker file
    if params.security_enabled:
      Directory(params.nm_security_marker_dir)
      File(params.nm_security_marker,
           content="Marker file to track first start after enabling/disabling security. "
                   "During first start yarn local, log dirs are removed and recreated"
           )
    elif not params.security_enabled:
      File(params.nm_security_marker, action="delete")

  if not params.security_enabled or params.toggle_nm_security:
    # handle_mounted_dirs ensures that we don't create dirs which are temporary unavailable (unmounted), and intended to reside on a different mount.
    nm_log_dir_to_mount_file_content = handle_mounted_dirs(create_log_dir, params.nm_log_dirs, params.nm_log_dir_to_mount_file, params)
    # create a history file used by handle_mounted_dirs
    File(params.nm_log_dir_to_mount_file,
         owner=params.hdfs_user,
         group=params.user_group,
         mode=0o644,
         content=nm_log_dir_to_mount_file_content
    )
    nm_local_dir_to_mount_file_content = handle_mounted_dirs(create_local_dir, params.nm_local_dirs, params.nm_local_dir_to_mount_file, params)
    File(params.nm_local_dir_to_mount_file,
         owner=params.hdfs_user,
         group=params.user_group,
         mode=0o644,
         content=nm_local_dir_to_mount_file_content
    )

def setup_resourcemanager():
  import params

  Directory(params.rm_nodes_exclude_dir,
       mode=0o755,
       create_parents=True,
       cd_access='a',
  )
  File(params.exclude_file_path,
       content=Template("exclude_hosts_list.j2"),
       owner=params.yarn_user,
       group=params.user_group
  )
  if params.include_hosts:
    Directory(params.rm_nodes_include_dir,
      mode=0o755,
      create_parents=True,
      cd_access='a',
    )
    File(params.include_file_path,
      content=Template("include_hosts_list.j2"),
      owner=params.yarn_user,
      group=params.user_group
    )
  # This depends on the parent directory already existing.
  File(params.yarn_job_summary_log,
     owner=params.yarn_user,
     group=params.user_group
  )
  if not is_empty(params.node_label_enable) and params.node_label_enable or is_empty(params.node_label_enable) and params.node_labels_dir:
    params.HdfsResource(params.node_labels_dir,
                         type="directory",
                         action="create_on_execute",
                         owner=params.yarn_user,
                         group=params.user_group,
                         mode=0o700
    )
    params.HdfsResource(None, action="execute")

def setup_ats():
  import params

  Directory(params.ats_leveldb_dir,
     owner=params.yarn_user,
     group=params.user_group,
     create_parents = True,
     cd_access="a",
  )

  # if stack support application timeline-service state store property (timeline_state_store stack feature)
  if params.stack_supports_timeline_state_store:
    Directory(params.ats_leveldb_state_store_dir,
     owner=params.yarn_user,
     group=params.user_group,
     create_parents = True,
     cd_access="a",
    )
  # app timeline server 1.5 directories
  if not is_empty(params.entity_groupfs_store_dir):
    parent_path = os.path.dirname(params.entity_groupfs_store_dir)
    params.HdfsResource(parent_path,
                        type="directory",
                        action="create_on_execute",
                        change_permissions_for_parents=True,
                        owner=params.yarn_user,
                        group=params.user_group,
                        mode=0o755
                        )
    params.HdfsResource(params.entity_groupfs_store_dir,
                        type="directory",
                        action="create_on_execute",
                        owner=params.yarn_user,
                        group=params.user_group,
                        mode=params.entity_groupfs_store_dir_mode
                        )
  if not is_empty(params.entity_groupfs_active_dir):
    parent_path = os.path.dirname(params.entity_groupfs_active_dir)
    params.HdfsResource(parent_path,
                        type="directory",
                        action="create_on_execute",
                        change_permissions_for_parents=True,
                        owner=params.yarn_user,
                        group=params.user_group,
                        mode=0o755
                        )
    params.HdfsResource(params.entity_groupfs_active_dir,
                        type="directory",
                        action="create_on_execute",
                        owner=params.yarn_user,
                        group=params.user_group,
                        mode=params.entity_groupfs_active_dir_mode
                        )
  if not is_empty(params.yarn_service_framework_path):
    parent_path = os.path.dirname(params.yarn_service_framework_path)
    params.HdfsResource(parent_path,
                        type="directory",
                        action="create_on_execute",
                        change_permissions_for_parents=True,
                        owner=params.yarn_user,
                        group=params.user_group,
                        mode=0o755
                        )
  params.HdfsResource(None, action="execute")

def create_log_dir(dir_name):
  import params
  Directory(dir_name,
            create_parents = True,
            cd_access="a",
            mode=0o775,
            owner=params.yarn_user,
            group=params.user_group,
            ignore_failures=True,
  )


def create_local_dir(dir_name):
  import params

  directory_args = {}

  if params.toggle_nm_security:
    directory_args["recursive_mode_flags"] = {'f': 'a+rw', 'd': 'a+rwx'}

  Directory(dir_name,
            create_parents=True,
            cd_access="a",
            mode=0o755,
            owner=params.yarn_user,
            group=params.user_group,
            ignore_failures=True,
            **directory_args
  )

@OsFamilyFuncImpl(os_family=OSConst.WINSRV_FAMILY)
def yarn(name = None):
  import params
  XmlConfig("mapred-site.xml",
            conf_dir=params.config_dir,
            configurations=params.config['configurations']['mapred-site'],
            owner=params.yarn_user,
            mode='f'
  )
  XmlConfig("yarn-site.xml",
            conf_dir=params.config_dir,
            configurations=params.config['configurations']['yarn-site'],
            owner=params.yarn_user,
            mode='f',
            configuration_attributes=params.config['configurationAttributes']['yarn-site']
  )
  XmlConfig("capacity-scheduler.xml",
            conf_dir=params.config_dir,
            configurations=params.config['configurations']['capacity-scheduler'],
            owner=params.yarn_user,
            mode='f'
  )

  XmlConfig("yarn-hbase-site.xml",
            conf_dir=params.config_dir,
            configurations=params.config['configurations']['yarn-hbase-site'],
            owner=params.yarn_user,
            mode='f'
  )

  if name in params.service_map:
    service_name = params.service_map[name]

    ServiceConfig(service_name,
                  action="change_user",
                  username = params.yarn_user,
                  password = Script.get_password(params.yarn_user))

def setup_atsv2_backend(name=None, config_dir=None):
    import params
    if not params.use_external_hbase and params.is_hbase_system_service_launch:
       if name == 'resourcemanager':
          setup_system_services(config_dir)
       elif name == 'nodemanager':
          setup_atsv2_hbase_files()

def setup_atsv2_hbase_files():
    import params
    if 'yarn-hbase-policy' in params.config['configurations']:
        XmlConfig( "hbase-policy.xml",
                   conf_dir = params.yarn_hbase_conf_dir,
                   configurations = params.config['configurations']['yarn-hbase-policy'],
                   configuration_attributes=params.config['configurationAttributes']['yarn-hbase-policy'],
                   owner = params.yarn_hbase_user,
                   group = params.user_group,
                   mode=0o644
                   )

    File(os.path.join(params.yarn_hbase_conf_dir, "hbase-env.sh"),
         owner=params.yarn_hbase_user,
         group=params.user_group,
         mode=0o644,
         content=InlineTemplate(params.yarn_hbase_env_sh_template)
    )

    File( format("{yarn_hbase_grant_premissions_file}"),
          owner   = params.yarn_hbase_user,
          group   = params.user_group,
          mode    = 0o644,
          content = Template('yarn_hbase_grant_permissions.j2')
          )

    if (params.yarn_hbase_log4j_props != None):
        File(format("{yarn_hbase_conf_dir}/log4j.properties"),
             mode=0o644,
             group=params.user_group,
             owner=params.yarn_hbase_user,
             content=InlineTemplate(params.yarn_hbase_log4j_props)
        )
    elif (os.path.exists(format("{yarn_hbase_conf_dir}/log4j.properties"))):
        File(format("{yarn_hbase_conf_dir}/log4j.properties"),
             mode=0o644,
             group=params.user_group,
             owner=params.yarn_hbase_user
        )
    if params.security_enabled:
        File(os.path.join(params.yarn_hbase_conf_dir, 'yarn_hbase_master_jaas.conf'),
             owner=params.yarn_hbase_user,
             group=params.user_group,
             content=Template("yarn_hbase_master_jaas.conf.j2")
        )
        File(os.path.join(params.yarn_hbase_conf_dir, 'yarn_hbase_regionserver_jaas.conf'),
             owner=params.yarn_hbase_user,
             group=params.user_group,
             content=Template("yarn_hbase_regionserver_jaas.conf.j2")
             )
    # Metrics properties
    if params.has_metric_collector:
      File(os.path.join(params.yarn_hbase_conf_dir, 'hadoop-metrics2-hbase.properties'),
           owner=params.yarn_hbase_user,
           group=params.user_group,
           content=Template("hadoop-metrics2-hbase.properties.j2")
           )

def setup_atsv2_hbase_directories():
    import  params
    Directory([params.yarn_hbase_pid_dir_prefix, params.yarn_hbase_pid_dir, params.yarn_hbase_log_dir],
              owner=params.yarn_hbase_user,
              group=params.user_group,
              create_parents=True,
              cd_access='a',
              )

    parent_dir = os.path.dirname(params.yarn_hbase_tmp_dir)
    # In case if we have several placeholders in path
    while ("${" in parent_dir):
        parent_dir = os.path.dirname(parent_dir)
    if parent_dir != os.path.abspath(os.sep) :
        Directory (parent_dir,
              create_parents = True,
              cd_access="a",
              )
        Execute(("chmod", "1777", parent_dir), sudo=True)

def setup_system_services(config_dir=None):
    import  params
    setup_atsv2_hbase_files()
    if params.security_enabled:
        File(os.path.join(params.yarn_hbase_conf_dir, 'hbase.yarnfile'),
             owner=params.yarn_hbase_user,
             group=params.user_group,
             content=Template("yarn_hbase_secure.yarnfile.j2")
             )
    else:
        File(os.path.join(params.yarn_hbase_conf_dir, 'hbase.yarnfile'),
             owner=params.yarn_hbase_user,
             group=params.user_group,
             content=Template("yarn_hbase_unsecure.yarnfile.j2")
             )


    user_dir = format("{yarn_system_service_dir}/{yarn_system_service_launch_mode}/{yarn_hbase_user}")
    params.HdfsResource(user_dir,
                        type="directory",
                        action="create_on_execute",
                        owner=params.yarn_user,
                        group=params.user_group
                        )
    params.HdfsResource(format("{user_dir}/hbase.yarnfile"),
                        type="file",
                        action="create_on_execute",
                        source=format("{yarn_hbase_conf_dir}/hbase.yarnfile"),
                        owner=params.yarn_user,
                        group=params.user_group
                        )
    params.HdfsResource(format("{yarn_hbase_user_home}"),
                        type="directory",
                        action="create_on_execute",
                        owner=params.yarn_hbase_user,
                        group=params.user_group,
                        mode=0o770,
                        )
    params.HdfsResource(format("{yarn_hbase_user_version_home}"),
                        type="directory",
                        action="create_on_execute",
                        owner=params.yarn_hbase_user,
                        group=params.user_group,
                        mode=0o770,
                        )
    params.HdfsResource(format("{yarn_hbase_user_version_home}/core-site.xml"),
                        type="file",
                        action="create_on_execute",
                        source=format("{config_dir}/core-site.xml"),
                        owner=params.yarn_hbase_user,
                        group=params.user_group
                        )
    params.HdfsResource(format("{yarn_hbase_user_version_home}/hbase-site.xml"),
                        type="file",
                        action="create_on_execute",
                        source=format("{yarn_hbase_conf_dir}/hbase-site.xml"),
                        owner=params.yarn_hbase_user,
                        group=params.user_group
                        )
    params.HdfsResource(format("{yarn_hbase_user_version_home}/hbase-policy.xml"),
                        type="file",
                        action="create_on_execute",
                        source=format("{yarn_hbase_conf_dir}/hbase-policy.xml"),
                        owner=params.yarn_hbase_user,
                        group=params.user_group
                        )
    params.HdfsResource(format("{yarn_hbase_user_version_home}/log4j.properties"),
                        type="file",
                        action="create_on_execute",
                        source=format("{yarn_hbase_conf_dir}/log4j.properties"),
                        owner=params.yarn_hbase_user,
                        group=params.user_group
                        )
    if params.has_metric_collector:
      params.HdfsResource(format("{yarn_hbase_user_version_home}/hadoop-metrics2-hbase.properties"),
                          type="file",
                          action="create_on_execute",
                          source=format("{yarn_hbase_conf_dir}/hadoop-metrics2-hbase.properties"),
                          owner=params.yarn_hbase_user,
                          group=params.user_group
                          )
    params.HdfsResource(params.yarn_hbase_hdfs_root_dir,
                        type="directory",
                        action="create_on_execute",
                        owner=params.yarn_hbase_user
                        )
    # copy service-dep.tar.gz into hdfs
    params.HdfsResource(params.yarn_service_app_hdfs_path,
                        type="directory",
                        action="create_on_execute",
                        owner=params.yarn_user,
                        group=params.user_group,
<<<<<<< HEAD
                        mode=0o555,
=======
                        mode=0o755,
>>>>>>> 2.7.11.0-python3
                        )
    params.HdfsResource(format("{yarn_service_app_hdfs_path}/service-dep.tar.gz"),
                    type="file",
                    action="create_on_execute",
                    source=format("{yarn_service_dep_source_path}"),
                    owner=params.yarn_user,
                    group=params.user_group,
                    mode=0o444,
                    )

    params.HdfsResource(None, action="execute")

    create_hbase_package()
    copy_hbase_package_to_hdfs()
